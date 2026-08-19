from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
import sqlite3
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import yaml

from .cache_files import CacheFiles
from .db import Database
from .events import get_logger
from .secret_store import SecretStore, SecretStoreUnavailable
from .sources import (
    ResolvedSubscription,
    SourceName,
    SubscriptionSource,
    V2BoardSubscriptionSource,
)
from .v2board_client import V2BoardError, interstitial_category


logger = get_logger("refresher")


SCHEMES = (
    "ss://",
    "ssr://",
    "vmess://",
    "vless://",
    "trojan://",
    "hysteria2://",
    "hy2://",
    "tuic://",
    "anytls://",
    "socks://",
    "http://",
    "https://",
)


class InvalidSubscription(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedSubscription:
    payload: bytes
    node_count: int
    content_format: str


def _uri_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip().lower().startswith(SCHEMES))


def _decoded_base64(text: str) -> str | None:
    compact = "".join(text.split())
    try:
        raw = base64.b64decode(compact + "=" * (-len(compact) % 4), altchars=b"-_", validate=True)
        return raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def validate_subscription(payload: bytes, max_bytes: int) -> ValidatedSubscription:
    if len(payload) > max_bytes:
        raise InvalidSubscription("subscription is too large")
    if not payload:
        raise InvalidSubscription("subscription is empty")
    try:
        text = payload.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise InvalidSubscription("subscription is not UTF-8") from exc
    decoded = _decoded_base64(text)
    if decoded and (count := _uri_count(decoded)):
        return ValidatedSubscription(payload, count, "base64")
    if count := _uri_count(text):
        return ValidatedSubscription(payload, count, "uri-list")
    try:
        document = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise InvalidSubscription("subscription is not valid YAML") from exc
    proxies = document.get("proxies") if isinstance(document, dict) else None
    if isinstance(proxies, list) and proxies:
        return ValidatedSubscription(payload, len(proxies), "yaml")
    raise InvalidSubscription("subscription contains no supported nodes")


SAFE_RESPONSE_HEADERS = {"subscription-userinfo", "profile-update-interval"}
MAX_REDIRECTS = 3
# 整个下载流程（含重定向、DNS 重解析）的总墙钟时限，防止慢速滴流占用刷新锁。
DOWNLOAD_TOTAL_DEADLINE = 60.0
RESPONSE_CLASSIFICATION_BYTES = 64 * 1024
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
MIN_RETRY_SECONDS = 60

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


async def _resolve_host(hostname: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    results = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(result[4][0] for result in results))


@dataclass(frozen=True)
class RefreshResult:
    updated: bool
    current_digest: str | None
    node_count: int
    consecutive_failures: int
    source: SourceName | None


@dataclass(frozen=True)
class ProtocolTestResult:
    ok: bool
    error_category: str | None
    expires_at: float | None


@dataclass(frozen=True)
class CredentialUpdateResult:
    ok: bool
    node_count: int
    error_category: str | None


class UpstreamRefresher:
    def __init__(
        self,
        db: Database,
        cache: CacheFiles,
        sources: tuple[SubscriptionSource, ...],
        transport=None,
        max_bytes: int = 8 * 1024 * 1024,
        resolver: Resolver | None = None,
        allowed_download_cidrs: Sequence[str] = (),
        credential_store: SecretStore | None = None,
        on_refreshed: Callable[[], Awaitable[None]] | None = None,
    ):
        self.db, self.cache, self.sources = db, cache, sources
        self.transport, self.max_bytes = transport, max_bytes
        self.resolver = resolver or _resolve_host
        self.credential_store = credential_store
        self.on_refreshed = on_refreshed
        self.allowed_download_networks = tuple(
            ipaddress.ip_network(value) for value in allowed_download_cidrs
        )
        self._lock = asyncio.Lock()
        self._refreshing = False

    async def refresh(self) -> RefreshResult:
        async with self._lock:
            result = await self._refresh_locked(time.time())
        if result.updated:
            await self._notify_refreshed()
        return result

    async def refresh_if_stale(self, max_age_seconds: float) -> RefreshResult | None:
        if self._refreshing:
            return None
        state = self.db.runtime_state()
        if not self._is_stale(state, max_age_seconds):
            return None
        async with self._lock:
            if self._refreshing:
                return None
            state = self.db.runtime_state()
            if not self._is_stale(state, max_age_seconds):
                return None
            result = await self._refresh_locked(time.time())
        if result is not None and result.updated:
            await self._notify_refreshed()
        return result

    def _is_stale(self, state, max_age_seconds: float) -> bool:
        now = time.time()
        if now - (state["last_attempt_at"] or 0) < MIN_RETRY_SECONDS:
            return False
        last_success = state["last_success_at"] or 0
        return now - last_success >= self._stale_after(
            max_age_seconds, state["consecutive_failures"] or 0
        )

    @staticmethod
    def _stale_after(
        base_seconds: float,
        consecutive_failures: int,
        max_backoff: float = 12 * 3600,
    ) -> float:
        return min(base_seconds * (2 ** min(consecutive_failures, 4)), max_backoff)

    async def _refresh_locked(self, attempted_at: float) -> RefreshResult:
        self._refreshing = True
        try:
            for source in self.sources:
                result = await self._refresh_from_source(source, attempted_at)
                if result is not None:
                    return result
            self.db.record_refresh_failure("all_sources_failed", attempted_at)
            logger.warning("all subscription sources failed")
            state = self.db.runtime_state()
            return RefreshResult(
                False,
                state["current_digest"],
                state["node_count"],
                state["consecutive_failures"],
                None,
            )
        finally:
            self._refreshing = False

    async def test_protocol(self) -> ProtocolTestResult:
        async with self._lock:
            source = next((item for item in self.sources if item.name == "protocol"), None)
            if source is None:
                return ProtocolTestResult(False, "not_configured", None)
            try:
                resolved = await source.fetch()
            except (V2BoardError, httpx.HTTPError, httpx.InvalidURL, OSError) as exc:
                category = self._error_category(exc)
                self.db.record_protocol_failure(category)
                return ProtocolTestResult(False, category, None)
            self._record_protocol_resolution(resolved)
            return ProtocolTestResult(True, None, resolved.expires_at)

    async def update_protocol_credentials(
        self,
        username: str,
        password: str,
    ) -> CredentialUpdateResult:
        async with self._lock:
            result = await self._update_protocol_credentials_locked(username, password)
        if result.ok:
            await self._notify_refreshed()
        return result

    async def _update_protocol_credentials_locked(
        self,
        username: str,
        password: str,
    ) -> CredentialUpdateResult:
        if not username.strip() or not password:
            return CredentialUpdateResult(False, 0, "invalid_credentials")
        try:
            source = next((item for item in self.sources if item.name == "protocol"), None)
            if not isinstance(source, V2BoardSubscriptionSource):
                return CredentialUpdateResult(False, 0, "not_configured")
            resolved = await source.fetch(credentials=(username, password))
            self._record_protocol_resolution(resolved)
            validated, safe_headers = await self._download(resolved)
            if self.credential_store is None or not self.credential_store.available:
                return CredentialUpdateResult(False, 0, "secret_store_unavailable")
            # 先提交缓存/状态，再持久化新凭据：若提交失败不会留下半套新凭据。
            self._commit_success(validated, safe_headers, time.time(), "protocol")
            self.credential_store.put(
                "airport_credentials",
                json.dumps({"username": username, "password": password}, separators=(",", ":")),
            )
            return CredentialUpdateResult(True, validated.node_count, None)
        except (
            V2BoardError,
            httpx.HTTPError,
            httpx.InvalidURL,
            InvalidSubscription,
            OSError,
            sqlite3.Error,
            SecretStoreUnavailable,
        ) as exc:
            category = self._error_category(exc)
            if isinstance(exc, SecretStoreUnavailable):
                category = "secret_store_unavailable"
            self.db.record_protocol_failure(category)
            return CredentialUpdateResult(False, 0, category)

    async def _refresh_from_source(
        self,
        source: SubscriptionSource,
        attempted_at: float,
    ) -> RefreshResult | None:
        attempts = 2 if source.name == "protocol" else 1
        for attempt in range(attempts):
            try:
                resolved = await source.fetch()
                if source.name == "protocol":
                    self._record_protocol_resolution(resolved)
                validated, safe_headers = await self._download(resolved)
                digest = self._commit_success(validated, safe_headers, attempted_at, resolved.source)
                return RefreshResult(True, digest, validated.node_count, 0, resolved.source)
            except (
                V2BoardError,
                httpx.HTTPError,
                httpx.InvalidURL,
                InvalidSubscription,
                OSError,
            ) as exc:
                category = self._error_category(exc)
                logger.warning("source %s failed: %s", source.name, category)
                if source.name == "protocol":
                    self.db.record_protocol_failure(category)
                    if attempt == 0 and self._retry_auth(exc):
                        continue
                return None
        return None

    def _commit_success(
        self,
        validated: ValidatedSubscription,
        safe_headers: dict[str, str],
        attempted_at: float,
        source: str,
    ) -> str:
        digest = self.cache.publish_raw(validated.payload, safe_headers)
        previous = self.db.runtime_state()["current_digest"]
        self.db.record_refresh_success(
            digest,
            validated.node_count,
            validated.content_format,
            safe_headers,
            attempted_at,
            source,
        )
        self.cache.prune_raw({digest})
        if digest != previous:
            self.cache.clear_converted()
        logger.info(
            "refresh succeeded source=%s nodes=%d%s",
            source,
            validated.node_count,
            " (unchanged)" if digest == previous else "",
        )
        return digest

    async def _notify_refreshed(self) -> None:
        if self.on_refreshed is None:
            return
        try:
            await self.on_refreshed()
        except Exception:
            logger.exception("post-refresh integration hook failed")

    async def _download(
        self,
        resolved: ResolvedSubscription,
    ) -> tuple[ValidatedSubscription, dict[str, str]]:
        current_url = resolved.url.get_secret_value()
        invalid_url = False
        try:
            async with asyncio.timeout(DOWNLOAD_TOTAL_DEADLINE):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    follow_redirects=False,
                    timeout=20,
                    limits=httpx.Limits(max_keepalive_connections=0),
                ) as client:
                    for redirect_count in range(MAX_REDIRECTS + 1):
                        logical_url, hostname, addresses = await self._validate_download_url(
                            current_url
                        )
                        status_code, headers, body = await self._fetch_download_hop(
                            client,
                            logical_url,
                            hostname,
                            addresses,
                            resolved.user_agent,
                        )
                        location = headers.get("location")
                        if status_code in REDIRECT_STATUS_CODES and location:
                            if redirect_count == MAX_REDIRECTS:
                                raise InvalidSubscription("subscription has too many redirects")
                            current_url = self._redirect_url(str(logical_url), location)
                            continue

                        if not 200 <= status_code < 300:
                            if category := interstitial_category(body):
                                raise V2BoardError(category, "download")
                            if status_code in {401, 403}:
                                raise V2BoardError(
                                    "authentication",
                                    "download",
                                    retry_auth=True,
                                )
                            raise V2BoardError("http_error", "download")

                        try:
                            validated = validate_subscription(body, self.max_bytes)
                        except InvalidSubscription:
                            if category := interstitial_category(body):
                                raise V2BoardError(category, "download") from None
                            raise
                        safe_headers = {
                            key.lower(): value
                            for key, value in headers.items()
                            if key.lower() in SAFE_RESPONSE_HEADERS
                        }
                        return validated, safe_headers
        except TimeoutError:
            raise InvalidSubscription("subscription download timed out") from None
        except httpx.InvalidURL:
            invalid_url = True
        if invalid_url:
            raise InvalidSubscription("subscription URL is invalid") from None
        raise InvalidSubscription("subscription has too many redirects")

    async def _validate_download_url(
        self,
        value: str,
    ) -> tuple[httpx.URL, str, tuple[str, ...]]:
        if not value or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise InvalidSubscription("subscription URL is invalid")
        try:
            parsed = urlsplit(value)
            parsed.port
            url = httpx.URL(value)
            hostname = url.raw_host.decode("ascii")
            explicit_port = url.port
            port = explicit_port or 443
        except (ValueError, httpx.InvalidURL):
            raise InvalidSubscription("subscription URL is invalid") from None
        if (
            url.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or "#" in value
            or explicit_port == 0
        ):
            raise InvalidSubscription("subscription URL is invalid")

        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None:
            if not self._is_allowed_destination(literal):
                raise InvalidSubscription("subscription destination is not public")
            return url, hostname, (str(literal),)

        resolution_failed = False
        try:
            addresses = tuple(await self.resolver(hostname, port or 443))
        except OSError:
            resolution_failed = True
            addresses = ()
        if resolution_failed:
            raise httpx.ConnectError("DNS resolution failed") from None
        if not addresses:
            raise InvalidSubscription("subscription destination has no public address")
        try:
            parsed_addresses = tuple(ipaddress.ip_address(address) for address in addresses)
        except ValueError:
            raise InvalidSubscription("subscription destination has an invalid address") from None
        allowed_addresses = tuple(
            dict.fromkeys(
                str(address) for address in parsed_addresses if self._is_allowed_destination(address)
            )
        )
        if not allowed_addresses:
            raise InvalidSubscription("subscription destination is not public")
        return url, hostname, allowed_addresses

    def _is_allowed_destination(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        if address.is_global and not address.is_multicast:
            return True
        return any(address in network for network in self.allowed_download_networks)

    async def _fetch_download_hop(
        self,
        client: httpx.AsyncClient,
        logical_url: httpx.URL,
        hostname: str,
        addresses: tuple[str, ...],
        user_agent: str,
    ) -> tuple[int, httpx.Headers, bytes]:
        last_transport_error: httpx.TransportError | None = None
        for address in addresses:
            try:
                async with client.stream(
                    "GET",
                    logical_url.copy_with(host=address),
                    headers={
                        "Accept-Encoding": "identity",
                        "Host": logical_url.netloc.decode("ascii"),
                        "User-Agent": user_agent,
                    },
                    extensions={"sni_hostname": hostname},
                ) as response:
                    if response.status_code in REDIRECT_STATUS_CODES:
                        return response.status_code, response.headers, b""
                    if response.headers.get("content-encoding", "").strip().lower() not in {
                        "",
                        "identity",
                    }:
                        raise InvalidSubscription("encoded subscription response is not allowed")
                    limit = self.max_bytes if response.is_success else RESPONSE_CLASSIFICATION_BYTES
                    body = await self._read_body(response, limit)
                    return response.status_code, response.headers, body
            except httpx.TransportError as exc:
                last_transport_error = exc
        if last_transport_error is not None:
            raise last_transport_error
        raise InvalidSubscription("subscription destination has no public address")

    @staticmethod
    def _redirect_url(current_url: str, location: str) -> str:
        if not location or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in location
        ):
            raise InvalidSubscription("subscription redirect is invalid")
        try:
            return str(httpx.URL(current_url).join(location))
        except httpx.InvalidURL:
            raise InvalidSubscription("subscription redirect is invalid") from None

    @staticmethod
    async def _read_body(response: httpx.Response, limit: int) -> bytes:
        if response.is_stream_consumed:
            if len(response.content) > limit:
                raise InvalidSubscription("subscription is too large")
            return response.content
        body = bytearray()
        async for chunk in response.aiter_raw():
            if len(body) + len(chunk) > limit:
                raise InvalidSubscription("subscription is too large")
            body.extend(chunk)
        return bytes(body)

    def _record_protocol_resolution(self, resolved: ResolvedSubscription) -> None:
        if resolved.login_succeeded_at is None or resolved.resolved_at is None:
            return
        self.db.record_protocol_success(
            resolved.login_succeeded_at,
            resolved.resolved_at,
            resolved.expires_at,
        )

    @staticmethod
    def _retry_auth(exc: Exception) -> bool:
        if isinstance(exc, V2BoardError):
            return exc.retry_auth
        return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 403}

    @staticmethod
    def _error_category(exc: Exception) -> str:
        if isinstance(exc, V2BoardError):
            return exc.category
        if isinstance(exc, httpx.HTTPStatusError):
            if exc.response.status_code in {401, 403}:
                return "authentication"
            return "http_error"
        if isinstance(exc, httpx.InvalidURL):
            return "invalid_subscription"
        if isinstance(exc, httpx.HTTPError):
            return "network"
        if isinstance(exc, InvalidSubscription):
            return "invalid_subscription"
        return "io_error"

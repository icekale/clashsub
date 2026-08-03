from __future__ import annotations

import asyncio
import socket
import ssl
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from urllib.parse import quote

import httpx
import yaml

from .cache_files import CacheFiles
from .db import Database
from .events import get_logger


logger = get_logger("health")

TLS_PROTOCOLS = {"trojan", "vless", "anytls", "hysteria2", "hy2", "tuic", "wireguard"}
DOH_URL = "https://223.5.5.5/resolve"

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


@dataclass(frozen=True)
class HealthSummary:
    total: int
    online: int
    checked_at: float | None


async def _resolve_via_doh(hostname: str, client: httpx.AsyncClient) -> tuple[str, ...]:
    try:
        response = await client.get(
            DOH_URL,
            params={"name": hostname, "type": "A"},
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        answers = payload.get("Answer") if isinstance(payload, dict) else None
        if not isinstance(answers, list):
            return ()
        return tuple(
            dict.fromkeys(
                str(entry["data"])
                for entry in answers
                if isinstance(entry, dict) and entry.get("type") == 1 and entry.get("data")
            )
        )
    except (httpx.HTTPError, ValueError, TypeError):
        return ()


async def _resolve_host(hostname: str, port: int) -> tuple[str, ...]:
    async with httpx.AsyncClient(timeout=5) as client:
        addresses = await _resolve_via_doh(hostname, client)
    if addresses:
        return addresses
    loop = asyncio.get_running_loop()
    try:
        results = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        return ()
    return tuple(dict.fromkeys(result[4][0] for result in results))


def _needs_tls(proxy: dict) -> bool:
    if proxy.get("tls"):
        return True
    return proxy.get("type") in TLS_PROTOCOLS


def _sni(proxy: dict) -> str | None:
    sni = proxy.get("servername") or proxy.get("sni")
    if sni:
        return str(sni)
    server = proxy.get("server")
    return str(server) if server else None


async def _probe_address(host: str, port: int, use_tls: bool, sni: str | None, timeout: float) -> float:
    start = time.monotonic()
    if use_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context, server_hostname=sni or host),
            timeout=timeout,
        )
    else:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass
    return (time.monotonic() - start) * 1000


class NodeHealthChecker:
    def __init__(
        self,
        db: Database,
        cache: CacheFiles,
        resolver: Resolver | None = None,
        max_concurrency: int = 20,
        timeout_seconds: float = 5,
    ):
        self.db, self.cache = db, cache
        self.resolver = resolver or _resolve_host
        self.max_concurrency = max_concurrency
        self.timeout_seconds = timeout_seconds

    async def run_once(self, timeout_seconds: float | None = None) -> HealthSummary:
        state = self.db.runtime_state()
        digest = state["current_digest"] if state else None
        if not digest:
            return HealthSummary(0, 0, None)
        try:
            snapshot = self.cache.read_raw(digest)
            document = yaml.safe_load(snapshot.payload)
        except (OSError, yaml.YAMLError, AttributeError):
            return HealthSummary(0, 0, None)
        proxies = document.get("proxies") if isinstance(document, dict) else None
        if not isinstance(proxies, list):
            return HealthSummary(0, 0, None)

        semaphore = asyncio.Semaphore(self.max_concurrency)
        checked_at = time.time()
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        resolution_cache: dict[str, tuple[str, ...]] = {}

        async def check_one(proxy) -> tuple[str, int, float | None, float] | None:
            name = str(proxy.get("name", "")).strip()
            if not name:
                return None
            try:
                server = str(proxy["server"]).strip()
                port = int(proxy["port"])
            except (KeyError, TypeError, ValueError):
                return (name, 0, None, checked_at)
            use_tls = _needs_tls(proxy)
            sni = _sni(proxy)
            async with semaphore:
                if server not in resolution_cache:
                    try:
                        resolution_cache[server] = tuple(
                            await asyncio.wait_for(self.resolver(server, port), timeout=timeout)
                        )
                    except (asyncio.TimeoutError, OSError):
                        resolution_cache[server] = ()
                addresses = resolution_cache[server]
                if not addresses:
                    return (name, 0, None, checked_at)
                for address in addresses:
                    try:
                        latency = await _probe_address(address, port, use_tls, sni, timeout)
                        return (name, 1, latency, checked_at)
                    except (OSError, asyncio.TimeoutError, ssl.SSLError):
                        continue
            return (name, 0, None, checked_at)

        results = await asyncio.gather(*(check_one(proxy) for proxy in proxies))
        records = [result for result in results if result is not None]
        self.db.replace_node_health(records)
        online = sum(1 for _, ok, _, _ in records if ok)
        logger.info("node health checked total=%d online=%d", len(records), online)
        return HealthSummary(len(records), online, checked_at)

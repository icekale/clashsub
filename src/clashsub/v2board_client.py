from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr


V2BOARD_USER_AGENT = "BBGen2UA"
MAX_API_RESPONSE_BYTES = 64 * 1024
_CAPTCHA_MARKERS = ("captcha", "recaptcha", "turnstile", "人机")
_CHALLENGE_MARKERS = ("<html", "<!doctype html", "just a moment", "cf-chl-")
_INVALID_JSON = object()


@dataclass(frozen=True)
class V2BoardSubscription:
    url: SecretStr = field(repr=False)
    expires_at: float | None
    login_succeeded_at: float
    resolved_at: float
    user_agent: str = V2BOARD_USER_AGENT


class V2BoardError(RuntimeError):
    def __init__(self, category: str, stage: str, retry_auth: bool = False):
        super().__init__(category)
        self.category = category
        self.stage = stage
        self.retry_auth = retry_auth


class V2BoardClient:
    def __init__(
        self,
        api_base_url: str,
        email: SecretStr,
        password: SecretStr,
        transport=None,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.email = email
        self.password = password
        self.transport = transport

    async def fetch_subscription(
        self,
        credentials: tuple[str, str] | tuple[SecretStr, SecretStr] | None = None,
    ) -> V2BoardSubscription:
        email, password = credentials or (self.email, self.password)
        email_value = email.get_secret_value() if isinstance(email, SecretStr) else email
        password_value = password.get_secret_value() if isinstance(password, SecretStr) else password
        async with httpx.AsyncClient(
            transport=self.transport,
            follow_redirects=False,
            timeout=20,
            headers={"User-Agent": V2BOARD_USER_AGENT, "Accept-Encoding": "identity"},
        ) as client:
            login = await self._request_json(
                client,
                "POST",
                f"{self.api_base_url}/passport/auth/login",
                "login",
                json={
                    "email": email_value,
                    "password": password_value,
                },
            )
            auth_data = self._required_string(login, "auth_data", "login", preserve=True)
            login_succeeded_at = time.time()
            subscription = await self._request_json(
                client,
                "GET",
                f"{self.api_base_url}/user/getSubscribe",
                "subscribe",
                headers={"authorization": auth_data},
            )
            subscribe_url = self._required_string(subscription, "subscribe_url", "subscribe")
            expires_at = self._expires_at(subscription.get("expired_at"))
            resolved_at = time.time()

        return V2BoardSubscription(
            SecretStr(self._clash_url(subscribe_url)),
            expires_at,
            login_succeeded_at,
            resolved_at,
        )

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        stage: str,
        **kwargs,
    ) -> dict:
        network_failure = False
        try:
            async with client.stream(method, url, **kwargs) as response:
                if response.headers.get("content-encoding", "").strip().lower() not in {
                    "",
                    "identity",
                }:
                    raise V2BoardError("invalid_response", stage)
                if response.is_stream_consumed:
                    if len(response.content) > MAX_API_RESPONSE_BYTES:
                        raise V2BoardError("invalid_response", stage)
                    body = bytearray(response.content)
                else:
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(body) + len(chunk) > MAX_API_RESPONSE_BYTES:
                            raise V2BoardError("invalid_response", stage)
                        body.extend(chunk)
        except (httpx.HTTPError, httpx.InvalidURL):
            network_failure = True
        if network_failure:
            raise V2BoardError("network", stage) from None

        try:
            document = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            document = _INVALID_JSON
        if isinstance(document, dict):
            message = document.get("message")
            if isinstance(message, str):
                lowered_message = message.lower()
                if any(marker in lowered_message for marker in _CAPTCHA_MARKERS) or (
                    "验证" in lowered_message and "身份验证" not in lowered_message
                ):
                    raise V2BoardError("captcha_required", stage)
        elif document is _INVALID_JSON:
            prefix = bytes(body[:4096]).decode("utf-8", errors="ignore").lower()
            if any(marker in prefix for marker in _CHALLENGE_MARKERS):
                raise V2BoardError("challenge", stage)
        if response.status_code in {401, 403}:
            raise V2BoardError("authentication", stage, retry_auth=stage == "subscribe")
        if not 200 <= response.status_code < 300:
            raise V2BoardError("http_error", stage)
        if not isinstance(document, dict):
            raise V2BoardError("invalid_response", stage)
        data = document.get("data")
        if not isinstance(data, dict):
            raise V2BoardError("invalid_response", stage)
        return data

    @staticmethod
    def _required_string(data: dict, key: str, stage: str, preserve: bool = False) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise V2BoardError("invalid_response", stage)
        return value if preserve else value.strip()

    @staticmethod
    def _expires_at(value) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise V2BoardError("invalid_response", "subscribe")
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise V2BoardError("invalid_response", "subscribe") from None
        if not math.isfinite(parsed):
            raise V2BoardError("invalid_response", "subscribe")
        return parsed

    @staticmethod
    def _clash_url(value: str) -> str:
        has_unsafe_character = any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
        if has_unsafe_character:
            raise V2BoardError("invalid_response", "subscribe")
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            port = parsed.port
            httpx.URL(value)
        except (ValueError, httpx.InvalidURL):
            raise V2BoardError("invalid_response", "subscribe") from None
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and port == 0)
        ):
            raise V2BoardError("invalid_response", "subscribe")
        without_fragment = value.split("#", 1)[0]
        if parsed.query:
            return f"{without_fragment}&flag=clash"
        if without_fragment.endswith("?"):
            return f"{without_fragment}flag=clash"
        return f"{without_fragment}?flag=clash"

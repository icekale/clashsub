from __future__ import annotations

import httpx


class OpenClashError(RuntimeError):
    pass


class OpenClashClient:
    def __init__(self, base_url: str, secret: str, transport=None, timeout: float = 10):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.transport = transport
        self.timeout = timeout

    async def version(self) -> dict:
        return await self._request("GET", "/version")

    async def refresh_provider(self, name: str) -> dict:
        # 只允许 URL 安全字符；`.`/`..`/`%`/`\` 会被 httpx 规范化或解码，
        # 可能意外指向别的资源。
        if not name or not all(character.isalnum() or character in "_.-" for character in name):
            raise OpenClashError("invalid provider name")
        if name in {".", ".."} or ".." in name:
            raise OpenClashError("invalid provider name")
        return await self._request(
            "PUT",
            f"/providers/proxies/{name}",
            params={"force": "true"},
        )

    async def _request(self, method: str, path: str, params=None) -> dict:
        headers = {"Authorization": f"Bearer {self.secret}"}
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.timeout,
            ) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise OpenClashError("network") from exc
        if response.status_code == 401:
            raise OpenClashError("unauthorized")
        if not 200 <= response.status_code < 300:
            raise OpenClashError(f"http_{response.status_code}")
        try:
            return response.json()
        except ValueError:
            return {}

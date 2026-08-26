from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class OpenClashError(RuntimeError):
    pass


def _safe_name(name: str, kind: str) -> str:
    if not name or not all(character.isalnum() or character in "_.-" for character in name):
        raise OpenClashError(f"invalid {kind} name")
    if name in {".", ".."} or ".." in name:
        raise OpenClashError(f"invalid {kind} name")
    return name


def ssh_key_usable(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _ssh_identity(path: Path) -> Path:
    # Compose on some hosts ignores secret uid/mode; OpenSSH rejects group/world-readable keys.
    if path.stat().st_mode & 0o077 == 0:
        return path
    dest = Path("/tmp/clashsub_openclash_ssh_key")
    dest.write_bytes(path.read_bytes())
    dest.chmod(0o600)
    return dest


class OpenClashClient:
    def __init__(
        self,
        base_url: str,
        secret: str,
        transport=None,
        timeout: float = 10,
        ssh_key_file: Path | None = None,
        ssh_timeout: float = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.transport = transport
        self.timeout = timeout
        self.ssh_key_file = Path(ssh_key_file) if ssh_key_file else None
        self.ssh_timeout = ssh_timeout

    async def version(self) -> dict:
        return await self._request("GET", "/version")

    async def refresh_provider(self, name: str) -> dict:
        # 只允许 URL 安全字符；`.`/`..`/`%`/`\` 会被 httpx 规范化或解码，
        # 可能意外指向别的资源。
        name = _safe_name(name, "provider")
        result = await self._request(
            "PUT",
            f"/providers/proxies/{name}",
            params={"force": "true"},
        )
        # Smart 内核会缓存节点选择；provider 内容变了但名字没变时，
        # 不刷缓存看起来就像订阅没更新。非 Smart 核心没有这个接口。
        try:
            await self._request("POST", "/cache/smart/flush")
        except OpenClashError:
            pass
        return result

    async def healthcheck_provider(self, name: str, timeout: float | None = None) -> dict[str, int]:
        name = _safe_name(name, "provider")
        wait = 120.0 if timeout is None else timeout
        await self._request("GET", f"/providers/proxies/{name}/healthcheck", timeout=wait)
        payload = await self._request("GET", f"/providers/proxies/{name}")
        if not str((payload or {}).get("testUrl") or "").strip():
            raise OpenClashError("healthcheck url missing")
        proxies = payload.get("proxies") if isinstance(payload, dict) else None
        if not isinstance(proxies, list) or not proxies:
            raise OpenClashError("empty provider")
        delays: dict[str, int] = {}
        for proxy in proxies:
            if not isinstance(proxy, dict):
                continue
            node = str(proxy.get("name", "")).strip()
            if not node:
                continue
            delay = 0
            history = proxy.get("history")
            if isinstance(history, list) and history and isinstance(history[-1], dict):
                raw = history[-1].get("delay")
                if isinstance(raw, int | float) and raw > 0:
                    delay = int(raw)
            delays[node] = delay
        if not delays:
            raise OpenClashError("empty provider")
        return delays

    async def update_config_subscribe(self, name: str) -> None:
        name = _safe_name(name, "subscribe")
        if not ssh_key_usable(self.ssh_key_file):
            raise OpenClashError("ssh key missing")
        host = urlsplit(self.base_url).hostname
        if not host:
            raise OpenClashError("invalid api url")
        identity = _ssh_identity(self.ssh_key_file)
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh",
                "-i",
                str(identity),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "UserKnownHostsFile=/tmp/clashsub_known_hosts",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "ConnectTimeout=10",
                f"root@{host}",
                "/usr/share/openclash/openclash.sh",
                name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise OpenClashError("ssh missing") from exc
        try:
            await asyncio.wait_for(proc.communicate(), timeout=self.ssh_timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise OpenClashError("ssh timeout") from exc
        if proc.returncode != 0:
            raise OpenClashError("ssh failed")

    async def _request(self, method: str, path: str, params=None, timeout: float | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.secret}"}
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.timeout if timeout is None else timeout,
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

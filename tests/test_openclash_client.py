import asyncio
from pathlib import Path

import httpx
import pytest

from clashsub.openclash_client import OpenClashClient, OpenClashError


@pytest.mark.asyncio
async def test_version_returns_payload():
    client = OpenClashClient(
        "http://192.168.1.1:9090/",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"meta": True, "version": "alpha"})
        ),
    )
    assert await client.version() == {"meta": True, "version": "alpha"}


@pytest.mark.asyncio
async def test_refresh_provider_sends_force_put_with_bearer():
    calls = []

    def handler(request):
        calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "auth": request.headers.get("authorization"),
            }
        )
        if request.url.path.endswith("/cache/smart/flush"):
            return httpx.Response(204)
        return httpx.Response(200, json={"updatedAt": "now"})

    client = OpenClashClient(
        "http://192.168.1.1:9090",
        "top-secret",
        transport=httpx.MockTransport(handler),
    )
    result = await client.refresh_provider("Provider_988009")
    assert result == {"updatedAt": "now"}
    assert calls == [
        {
            "method": "PUT",
            "url": "http://192.168.1.1:9090/providers/proxies/Provider_988009?force=true",
            "auth": "Bearer top-secret",
        },
        {
            "method": "POST",
            "url": "http://192.168.1.1:9090/cache/smart/flush",
            "auth": "Bearer top-secret",
        },
    ]


@pytest.mark.asyncio
async def test_refresh_provider_still_succeeds_when_smart_flush_missing():
    calls = []

    def handler(request):
        calls.append(request.method + " " + request.url.path)
        if request.url.path.endswith("/cache/smart/flush"):
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={"updatedAt": "now"})

    client = OpenClashClient(
        "http://192.168.1.1:9090",
        "top-secret",
        transport=httpx.MockTransport(handler),
    )
    result = await client.refresh_provider("Provider_988009")
    assert result == {"updatedAt": "now"}
    assert calls == [
        "PUT /providers/proxies/Provider_988009",
        "POST /cache/smart/flush",
    ]


@pytest.mark.asyncio
async def test_unauthorized_raises_openclash_error():
    client = OpenClashClient(
        "http://192.168.1.1:9090",
        "wrong",
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="unauthorized")),
    )
    with pytest.raises(OpenClashError, match="unauthorized"):
        await client.version()


@pytest.mark.asyncio
async def test_network_error_raises_openclash_error():
    def handler(request):
        raise httpx.ConnectError("down")

    client = OpenClashClient(
        "http://192.168.1.1:9090",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OpenClashError, match="network"):
        await client.version()


@pytest.mark.asyncio
async def test_healthcheck_provider_triggers_then_reads_delays():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/healthcheck"):
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={
                "name": "Provider_988009",
                "testUrl": "https://www.gstatic.com/generate_204",
                "proxies": [
                    {"name": "OK Node", "history": [{"delay": 120}]},
                    {"name": "Dead Node", "history": [{"delay": 0}]},
                    {"name": "No History", "alive": False},
                ],
            },
        )

    client = OpenClashClient(
        "http://192.168.1.1:9090",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    assert await client.healthcheck_provider("Provider_988009") == {
        "OK Node": 120,
        "Dead Node": 0,
        "No History": 0,
    }
    assert calls == [
        ("GET", "/providers/proxies/Provider_988009/healthcheck"),
        ("GET", "/providers/proxies/Provider_988009"),
    ]


@pytest.mark.asyncio
async def test_healthcheck_provider_rejects_missing_test_url():
    def handler(request):
        if request.url.path.endswith("/healthcheck"):
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={"name": "Provider_988009", "testUrl": "", "proxies": [{"name": "n", "history": []}]},
        )

    client = OpenClashClient(
        "http://192.168.1.1:9090",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OpenClashError, match="healthcheck url"):
        await client.healthcheck_provider("Provider_988009")


@pytest.mark.asyncio
async def test_invalid_provider_name_rejected():
    client = OpenClashClient("http://192.168.1.1:9090", "secret")
    with pytest.raises(OpenClashError, match="provider"):
        await client.refresh_provider("bad/name")


class _FakeProc:
    def __init__(self, returncode=0, hang=False):
        self.returncode = returncode
        self.hang = hang
        self.killed = False

    async def communicate(self):
        if self.hang:
            await asyncio.sleep(3600)
        return b"", b""

    def kill(self):
        self.killed = True
        self.hang = False


@pytest.mark.asyncio
async def test_update_config_subscribe_runs_ssh(tmp_path, monkeypatch):
    key = tmp_path / "id_ed25519"
    key.write_text("ssh-key", encoding="utf-8")
    key.chmod(0o600)
    seen = {}

    async def fake_exec(*args, **kwargs):
        seen["args"] = args
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    client = OpenClashClient(
        "http://192.168.5.1:9090",
        "secret",
        ssh_key_file=key,
    )
    await client.update_config_subscribe("sep_bbdmfetch")

    assert seen["args"][0] == "ssh"
    assert "-i" in seen["args"]
    assert str(key) in seen["args"]
    assert "root@192.168.5.1" in seen["args"]
    assert seen["args"][-2:] == ("/usr/share/openclash/openclash.sh", "sep_bbdmfetch")


@pytest.mark.asyncio
async def test_update_config_subscribe_copies_world_readable_key(tmp_path, monkeypatch):
    key = tmp_path / "id_ed25519"
    key.write_text("ssh-key", encoding="utf-8")
    key.chmod(0o644)
    seen = {}

    async def fake_exec(*args, **kwargs):
        seen["args"] = args
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    client = OpenClashClient("http://192.168.5.1:9090", "secret", ssh_key_file=key)
    await client.update_config_subscribe("sep_bbdmfetch")
    copied = Path("/tmp/clashsub_openclash_ssh_key")
    assert copied.is_file()
    assert copied.stat().st_mode & 0o077 == 0
    assert seen["args"][seen["args"].index("-i") + 1] == str(copied)
    copied.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_update_config_subscribe_rejects_invalid_name(tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("ssh-key", encoding="utf-8")
    key.chmod(0o600)
    client = OpenClashClient("http://192.168.5.1:9090", "secret", ssh_key_file=key)
    with pytest.raises(OpenClashError, match="subscribe"):
        await client.update_config_subscribe("sep_bbdmfetch.yaml;reboot")


@pytest.mark.asyncio
async def test_update_config_subscribe_requires_key(tmp_path):
    client = OpenClashClient("http://192.168.5.1:9090", "secret")
    with pytest.raises(OpenClashError, match="ssh key"):
        await client.update_config_subscribe("sep_bbdmfetch")

    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    client = OpenClashClient("http://192.168.5.1:9090", "secret", ssh_key_file=empty)
    with pytest.raises(OpenClashError, match="ssh key"):
        await client.update_config_subscribe("sep_bbdmfetch")


@pytest.mark.asyncio
async def test_update_config_subscribe_nonzero_exit_raises(tmp_path, monkeypatch):
    key = tmp_path / "id_ed25519"
    key.write_text("ssh-key", encoding="utf-8")
    key.chmod(0o600)

    async def fake_exec(*args, **kwargs):
        return _FakeProc(returncode=255)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    client = OpenClashClient("http://192.168.5.1:9090", "secret", ssh_key_file=key)
    with pytest.raises(OpenClashError, match="ssh failed"):
        await client.update_config_subscribe("sep_bbdmfetch")


@pytest.mark.asyncio
async def test_update_config_subscribe_timeout_kills_process(tmp_path, monkeypatch):
    key = tmp_path / "id_ed25519"
    key.write_text("ssh-key", encoding="utf-8")
    key.chmod(0o600)
    proc = _FakeProc(hang=True)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    client = OpenClashClient(
        "http://192.168.5.1:9090",
        "secret",
        ssh_key_file=key,
        ssh_timeout=0.01,
    )
    with pytest.raises(OpenClashError, match="ssh timeout"):
        await client.update_config_subscribe("sep_bbdmfetch")
    assert proc.killed is True

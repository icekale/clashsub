import asyncio
import base64
import time

import httpx
import pytest
from pydantic import SecretStr

from clashsub.cache_files import CacheFiles
from clashsub.db import Database
from clashsub.health import HealthSummary, NodeHealthChecker
from clashsub.integration import IntegrationService
from clashsub.openclash_client import OpenClashClient, OpenClashError
from clashsub.secret_store import SecretStore
from clashsub.settings import RuntimeSettings, SettingsStore
from clashsub.sources import StaticUrlSource
from clashsub.subscription import UpstreamRefresher


def _key_file(tmp_path):
    path = tmp_path / "key"
    path.write_text(base64.b64encode(b"k" * 32).decode(), encoding="ascii")
    return path


def _db(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    return db


async def _async_resolver(hostname, port):
    return ("93.184.216.34",)


class RecordingClient(OpenClashClient):
    def __init__(self, calls):
        self.calls = calls
        super().__init__("http://192.168.1.1:9090", "secret")

    async def refresh_provider(self, name):
        self.calls.append(name)
        return {"updatedAt": "now"}

    async def healthcheck_provider(self, name, timeout=None):
        raise OpenClashError("network")


@pytest.mark.asyncio
async def test_sync_after_refresh_pushes_provider(tmp_path, monkeypatch):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(RuntimeSettings(openclash_enabled=True, openclash_api_url="http://192.168.1.1:9090", openclash_provider="Provider_988009"))
    credentials = SecretStore(db, _key_file(tmp_path))
    credentials.put("openclash_api_secret", "top-secret")

    class FakeHealth:
        def __init__(self):
            self.runs = 0

        async def run_once(self, timeout_seconds=None):
            self.runs += 1
            return HealthSummary(0, 0, None)

    fake_health = FakeHealth()
    calls = []
    monkeypatch.setattr(
        "clashsub.integration.OpenClashClient",
        lambda *args, **kwargs: RecordingClient(calls),
    )
    integration = IntegrationService(store, credentials, fake_health)
    await integration.sync_after_refresh()

    assert calls == ["Provider_988009"]
    assert fake_health.runs == 0


@pytest.mark.asyncio
async def test_sync_after_refresh_updates_config_subscribe(tmp_path, monkeypatch):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            openclash_enabled=True,
            openclash_api_url="http://192.168.1.1:9090",
            openclash_provider="Provider_988009",
            openclash_subscribe_name="sep_bbdmfetch",
        )
    )
    credentials = SecretStore(db, _key_file(tmp_path))
    credentials.put("openclash_api_secret", "top-secret")
    key = tmp_path / "id_ed25519"
    key.write_text("ssh-key", encoding="utf-8")
    calls = []

    class RecordingSubscribeClient:
        async def refresh_provider(self, name):
            calls.append(name)
            return {"updatedAt": "now"}

        async def update_config_subscribe(self, name):
            calls.append(f"subscribe:{name}")

    monkeypatch.setattr(
        "clashsub.integration.OpenClashClient",
        lambda *args, **kwargs: RecordingSubscribeClient(),
    )
    integration = IntegrationService(
        store,
        credentials,
        NodeHealthChecker(db, CacheFiles(tmp_path / "cache")),
        ssh_key_file=key,
    )
    await integration.sync_after_refresh()
    assert calls == ["Provider_988009", "subscribe:sep_bbdmfetch"]


@pytest.mark.asyncio
async def test_sync_skips_subscribe_without_name(tmp_path, monkeypatch):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            openclash_enabled=True,
            openclash_api_url="http://192.168.1.1:9090",
            openclash_provider="Provider_988009",
        )
    )
    credentials = SecretStore(db, _key_file(tmp_path))
    credentials.put("openclash_api_secret", "top-secret")
    calls = []

    class RecordingSubscribeClient:
        async def refresh_provider(self, name):
            calls.append(name)
            return {"updatedAt": "now"}

        async def update_config_subscribe(self, name):
            calls.append(f"subscribe:{name}")

    monkeypatch.setattr(
        "clashsub.integration.OpenClashClient",
        lambda *args, **kwargs: RecordingSubscribeClient(),
    )
    integration = IntegrationService(
        store,
        credentials,
        NodeHealthChecker(db, CacheFiles(tmp_path / "cache")),
        ssh_key_file=tmp_path / "id_ed25519",
    )
    await integration.sync_after_refresh()
    assert calls == ["Provider_988009"]


@pytest.mark.asyncio
async def test_subscribe_failure_is_swallowed(tmp_path, monkeypatch):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            openclash_enabled=True,
            openclash_api_url="http://192.168.1.1:9090",
            openclash_provider="Provider_988009",
            openclash_subscribe_name="sep_bbdmfetch",
        )
    )
    credentials = SecretStore(db, _key_file(tmp_path))
    credentials.put("openclash_api_secret", "top-secret")
    key = tmp_path / "id_ed25519"
    key.write_text("ssh-key", encoding="utf-8")

    class FailingSubscribeClient:
        async def refresh_provider(self, name):
            return {"updatedAt": "now"}

        async def update_config_subscribe(self, name):
            raise OpenClashError("ssh failed")

    monkeypatch.setattr(
        "clashsub.integration.OpenClashClient",
        lambda *args, **kwargs: FailingSubscribeClient(),
    )
    integration = IntegrationService(
        store,
        credentials,
        NodeHealthChecker(db, CacheFiles(tmp_path / "cache")),
        ssh_key_file=key,
    )
    await integration.sync_after_refresh()


@pytest.mark.asyncio
async def test_sync_skips_push_without_secret(tmp_path, monkeypatch):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(RuntimeSettings(openclash_enabled=True, openclash_api_url="http://192.168.1.1:9090", openclash_provider="Provider_988009"))
    calls = []
    monkeypatch.setattr(
        "clashsub.integration.OpenClashClient",
        lambda *args, **kwargs: RecordingClient(calls),
    )
    integration = IntegrationService(
        store,
        SecretStore(db, _key_file(tmp_path)),
        NodeHealthChecker(db, CacheFiles(tmp_path / "cache")),
    )
    await integration.sync_after_refresh()
    assert calls == []


@pytest.mark.asyncio
async def test_push_failure_is_swallowed(tmp_path, monkeypatch):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(RuntimeSettings(openclash_enabled=True, openclash_api_url="http://192.168.1.1:9090", openclash_provider="Provider_988009"))
    credentials = SecretStore(db, _key_file(tmp_path))
    credentials.put("openclash_api_secret", "top-secret")

    class FailingClient:
        async def refresh_provider(self, name):
            raise OpenClashError("unauthorized")

    monkeypatch.setattr(
        "clashsub.integration.OpenClashClient",
        lambda *args, **kwargs: FailingClient(),
    )
    integration = IntegrationService(
        store,
        credentials,
        NodeHealthChecker(db, CacheFiles(tmp_path / "cache")),
    )
    await integration.sync_after_refresh()  # must not raise


@pytest.mark.asyncio
async def test_sync_runs_health_when_enabled(tmp_path, monkeypatch):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(RuntimeSettings(openclash_enabled=True, openclash_api_url="http://192.168.1.1:9090", openclash_provider="Provider_988009", health_enabled=True, health_timeout_seconds=3))
    credentials = SecretStore(db, _key_file(tmp_path))
    credentials.put("openclash_api_secret", "top-secret")

    class FakeHealth:
        def __init__(self):
            self.runs = 0
            self.timeout = None

        async def run_once(self, timeout_seconds=None):
            self.runs += 1
            self.timeout = timeout_seconds
            return HealthSummary(2, 1, time.time())

    fake_health = FakeHealth()
    calls = []
    monkeypatch.setattr(
        "clashsub.integration.OpenClashClient",
        lambda *args, **kwargs: RecordingClient(calls),
    )
    integration = IntegrationService(store, credentials, fake_health)
    await integration.sync_after_refresh()

    assert fake_health.runs == 1
    assert fake_health.timeout == 3
    assert calls == ["Provider_988009"]


class RecordingRefresher:
    def __init__(self):
        self.calls = 0

    async def refresh(self):
        self.calls += 1


class StubHealth:
    def __init__(self, summary):
        self.summary = summary

    async def run_once(self, timeout_seconds=None):
        return self.summary


def _publish_nodes(tmp_path, db, names):
    cache = CacheFiles(tmp_path / "cache")
    lines = ["proxies:\n"]
    for name in names:
        lines.append(
            f"  - {{name: {name}, type: ss, server: 127.0.0.1, port: 1}}\n"
        )
    digest = cache.publish_raw("".join(lines).encode(), {})
    db.record_refresh_success(digest, len(names), "yaml", {}, time.time(), "test")
    return cache


@pytest.mark.asyncio
async def test_run_health_uses_openclash_provider_delays(tmp_path, monkeypatch):
    db = _db(tmp_path)
    cache = _publish_nodes(tmp_path, db, ["ok", "dead"])
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            health_enabled=True,
            openclash_enabled=True,
            openclash_api_url="http://192.168.1.1:9090",
            openclash_provider="Provider_988009",
        )
    )
    credentials = SecretStore(db, _key_file(tmp_path))
    credentials.put("openclash_api_secret", "top-secret")

    class DelayClient:
        async def healthcheck_provider(self, name, timeout=None):
            assert name == "Provider_988009"
            return {"ok": 90, "dead": 0}

    monkeypatch.setattr(
        "clashsub.integration.OpenClashClient",
        lambda *args, **kwargs: DelayClient(),
    )
    checker = NodeHealthChecker(db, cache)

    async def should_not_handshake(timeout_seconds=None):
        raise AssertionError("handshake must not run when provider delays work")

    checker.run_once = should_not_handshake
    integration = IntegrationService(store, credentials, checker)
    summary = await integration.run_health()

    assert (summary.total, summary.online) == (2, 1)
    rows = {row["name"]: row for row in db.list_node_health()}
    assert rows["ok"]["ok"] == 1
    assert rows["dead"]["ok"] == 0


@pytest.mark.asyncio
async def test_run_health_falls_back_to_handshake_when_openclash_fails(tmp_path, monkeypatch):
    db = _db(tmp_path)
    cache = _publish_nodes(tmp_path, db, ["ss"])
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            health_enabled=True,
            openclash_enabled=True,
            openclash_api_url="http://192.168.1.1:9090",
            openclash_provider="Provider_988009",
        )
    )
    credentials = SecretStore(db, _key_file(tmp_path))
    credentials.put("openclash_api_secret", "top-secret")

    class FailingDelayClient:
        async def healthcheck_provider(self, name, timeout=None):
            raise OpenClashError("http_503")

    monkeypatch.setattr(
        "clashsub.integration.OpenClashClient",
        lambda *args, **kwargs: FailingDelayClient(),
    )
    async def resolver(hostname, port):
        return ("127.0.0.1",)

    checker = NodeHealthChecker(db, cache, resolver=resolver, timeout_seconds=1)
    integration = IntegrationService(store, credentials, checker)
    summary = await integration.run_health()

    assert summary.total == 1
    assert summary.online == 0


@pytest.mark.asyncio
async def test_auto_refresh_triggers_when_nodes_unavailable(tmp_path):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            health_enabled=True,
            health_refresh_enabled=True,
            health_refresh_online_ratio=0.5,
            health_refresh_cooldown_minutes=10,
        )
    )
    refresher = RecordingRefresher()
    integration = IntegrationService(
        store,
        SecretStore(db, _key_file(tmp_path)),
        StubHealth(HealthSummary(49, 10, time.time())),
        refresher=refresher,
    )

    await integration.run_health()

    assert refresher.calls == 1


@pytest.mark.asyncio
async def test_auto_refresh_skips_when_healthy(tmp_path):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            health_enabled=True,
            health_refresh_enabled=True,
            health_refresh_online_ratio=0.5,
            health_refresh_cooldown_minutes=10,
        )
    )
    refresher = RecordingRefresher()
    integration = IntegrationService(
        store,
        SecretStore(db, _key_file(tmp_path)),
        StubHealth(HealthSummary(49, 40, time.time())),
        refresher=refresher,
    )

    await integration.run_health()

    assert refresher.calls == 0


@pytest.mark.asyncio
async def test_auto_refresh_respects_cooldown(tmp_path):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            health_enabled=True,
            health_refresh_enabled=True,
            health_refresh_online_ratio=0.5,
            health_refresh_cooldown_minutes=10,
        )
    )
    refresher = RecordingRefresher()
    integration = IntegrationService(
        store,
        SecretStore(db, _key_file(tmp_path)),
        StubHealth(HealthSummary(49, 10, time.time())),
        refresher=refresher,
    )

    await integration.run_health()
    await integration.run_health()

    assert refresher.calls == 1


@pytest.mark.asyncio
async def test_auto_refresh_skips_when_disabled_or_unwired(tmp_path):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            health_enabled=True,
            health_refresh_enabled=False,
            health_refresh_online_ratio=0.5,
            health_refresh_cooldown_minutes=10,
        )
    )
    integration = IntegrationService(
        store,
        SecretStore(db, _key_file(tmp_path)),
        StubHealth(HealthSummary(49, 10, time.time())),
    )
    await integration.run_health()  # no refresher wired: must not raise


@pytest.mark.asyncio
async def test_auto_refresh_swallows_refresher_failure(tmp_path):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            health_enabled=True,
            health_refresh_enabled=True,
            health_refresh_online_ratio=0.5,
            health_refresh_cooldown_minutes=10,
        )
    )

    class FailingRefresher:
        async def refresh(self):
            raise RuntimeError("airport down")

    integration = IntegrationService(
        store,
        SecretStore(db, _key_file(tmp_path)),
        StubHealth(HealthSummary(49, 5, time.time())),
        refresher=FailingRefresher(),
    )

    await integration.run_health()  # must not raise


@pytest.mark.asyncio
async def test_push_now_raises_when_not_configured(tmp_path):
    db = _db(tmp_path)
    integration = IntegrationService(
        SettingsStore(db),
        SecretStore(db, _key_file(tmp_path)),
        NodeHealthChecker(db, CacheFiles(tmp_path / "cache")),
    )
    with pytest.raises(OpenClashError, match="disabled|not configured"):
        await integration.push_now()


@pytest.mark.asyncio
async def test_refresh_triggered_hook_runs_after_success(tmp_path):
    db = _db(tmp_path)
    db.initialize()
    events = []

    async def hook():
        events.append("hook")

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            text="proxies:\n  - {name: one, type: trojan, server: node.example, port: 443, password: pass}\n",
        )
    )
    refresher = UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        (StaticUrlSource(SecretStr("https://provider.example/sub?token=x")),),
        transport=transport,
        resolver=_async_resolver,
        on_refreshed=hook,
    )
    result = await refresher.refresh()
    assert result.updated is True
    assert events == ["hook"]


@pytest.mark.asyncio
async def test_auto_refresh_via_refresh_hook_does_not_deadlock(tmp_path):
    db = _db(tmp_path)
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            health_enabled=True,
            health_refresh_enabled=True,
            health_refresh_online_ratio=0.5,
            health_refresh_cooldown_minutes=10,
        )
    )
    fetched = {"count": 0}
    payload = (
        "proxies:\n"
        "  - {name: one, type: trojan, server: node.example, port: 443, password: pass}\n"
    )

    def handler(request):
        fetched["count"] += 1
        return httpx.Response(200, text=payload)

    refresher = UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        (StaticUrlSource(SecretStr("https://provider.example/sub?token=x")),),
        transport=httpx.MockTransport(handler),
        resolver=_async_resolver,
    )
    integration = IntegrationService(
        store,
        SecretStore(db, _key_file(tmp_path)),
        StubHealth(HealthSummary(49, 5, time.time())),
        refresher=refresher,
    )
    refresher.on_refreshed = integration.sync_after_refresh

    result = await asyncio.wait_for(refresher.refresh(), timeout=5)

    assert result.updated is True
    # degraded health triggers one auto refresh through the hook; both complete.
    assert fetched["count"] == 2

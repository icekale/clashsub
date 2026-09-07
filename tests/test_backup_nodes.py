import base64
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from clashsub.app import create_app
from clashsub.backup_nodes import BackupNodes, parse_backup_nodes
from clashsub.cache_files import CacheFiles
from clashsub.db import Database
from clashsub.secret_store import SecretStore
from clashsub.settings import RuntimeSettings, SettingsStore
from clashsub.sources import ResolvedSubscription
from clashsub.subscription import InvalidSubscription, UpstreamRefresher


BACKUP = "trojan://bak@node.example:443#bak\nss://YWVzLTI1Ni1nY206cGFzcw@node.example:8388#ss"


def _key_file(tmp_path):
    import base64
    path = tmp_path / "key"
    path.write_bytes(base64.b64encode(b"k" * 32))
    return path


def _nodes(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    store = SecretStore(db, _key_file(tmp_path))
    settings = SettingsStore(db)
    cache = CacheFiles(tmp_path / "cache")
    return db, BackupNodes(db, cache, store, settings)


def test_parse_strips_comments_and_counts_nodes():
    parsed = parse_backup_nodes("# comment\n\n" + BACKUP + "\n")
    assert parsed.node_count == 2
    assert parsed.content_format == "uri-list"


def test_parse_rejects_garbage():
    with pytest.raises(InvalidSubscription):
        parse_backup_nodes("not-a-proxy")


YAML = """# keep me
proxies:
  - name: bak
    type: ss
    server: node.example
    port: 8388
    cipher: aes-256-gcm
    password: p
"""


def test_parse_yaml_keeps_comments_and_indent():
    parsed = parse_backup_nodes(YAML)
    assert parsed.content_format == "yaml"
    assert parsed.node_count == 1
    assert parsed.payload.decode("utf-8") == YAML


def test_parse_yaml_without_proxies_rejected():
    with pytest.raises(InvalidSubscription):
        parse_backup_nodes("proxies: []\n")


def test_save_yaml_roundtrip(tmp_path):
    _, nodes = _nodes(tmp_path)
    nodes.save(YAML)
    assert nodes.status()["nodes"] == YAML
    assert nodes.status()["node_count"] == 1


def test_inactive_below_threshold(tmp_path):
    db, nodes = _nodes(tmp_path)
    nodes.save(BACKUP)
    db.record_refresh_failure("all_sources_failed", 1)
    assert nodes.is_active() is False


def test_active_at_threshold(tmp_path):
    db, nodes = _nodes(tmp_path)
    nodes.save(BACKUP)
    for i in range(3):
        db.record_refresh_failure("all_sources_failed", i)
    assert nodes.is_active() is True
    snap = nodes.snapshot()
    assert b"trojan://bak@" in snap.payload
    assert snap.safe_headers == {}


def test_empty_save_disables_even_at_threshold(tmp_path):
    db, nodes = _nodes(tmp_path)
    nodes.save(BACKUP)
    for i in range(3):
        db.record_refresh_failure("all_sources_failed", i)
    result = nodes.save("  \n# only comments\n")
    assert result["configured"] is False
    assert nodes.is_active() is False


def _stale_converted(nodes):
    converted = nodes.cache.root / "converted"
    converted.mkdir(parents=True, exist_ok=True)
    stale = converted / "stale.yaml"
    stale.write_text("old", encoding="utf-8")
    return stale


def test_save_clears_converted_only_when_activation_changes(tmp_path):
    db, nodes = _nodes(tmp_path / "below")
    stale = _stale_converted(nodes)
    nodes.save(BACKUP)
    assert stale.exists()  # still inactive → no clear

    db, nodes = _nodes(tmp_path / "at")
    for i in range(3):
        db.record_refresh_failure("all_sources_failed", i)
    stale = _stale_converted(nodes)
    nodes.save(BACKUP)
    assert not stale.exists()  # becomes active → cleared

    db, nodes = _nodes(tmp_path / "off")
    nodes.save(BACKUP)
    for i in range(3):
        db.record_refresh_failure("all_sources_failed", i)
    stale = _stale_converted(nodes)
    nodes.save("  \n# only comments\n")
    assert not stale.exists()  # becomes inactive → cleared


class FakeSource:
    def __init__(self, name, results):
        self.name = name
        self.results = list(results)
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


async def _public_resolver(hostname: str, port: int):
    return ("93.184.216.34",)


@pytest.mark.asyncio
async def test_refresh_notifies_only_when_crossing_threshold(tmp_path):
    db, nodes = _nodes(tmp_path)
    nodes.save(BACKUP)
    notified = []

    async def hook():
        notified.append(True)

    source = FakeSource("fallback", [InvalidSubscription("down")] * 4)
    refresher = UpstreamRefresher(
        db,
        nodes.cache,
        (source,),
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
        resolver=_public_resolver,
        backup=nodes,
        on_refreshed=hook,
    )
    await refresher.refresh()
    await refresher.refresh()
    assert notified == []
    await refresher.refresh()
    assert len(notified) == 1
    digest = db.runtime_state()["current_digest"]
    assert digest is None


@pytest.mark.asyncio
async def test_success_clears_converted_when_leaving_backup(tmp_path):
    db, nodes = _nodes(tmp_path)
    payload = b"trojan://air@node.example:443#air\n"
    digest = nodes.cache.publish_raw(payload, {})
    db.record_refresh_success(digest, 1, "uri-list", {}, 1, "fallback")
    nodes.save(BACKUP)
    for i in range(3):
        db.record_refresh_failure("all_sources_failed", i + 2)
    converted = nodes.cache.root / "converted"
    converted.mkdir(parents=True, exist_ok=True)
    stale = converted / "stale.yaml"
    stale.write_text("old", encoding="utf-8")

    good = payload
    source = FakeSource(
        "fallback",
        [
            ResolvedSubscription(
                "fallback",
                SecretStr("https://sub.invalid/one"),
                user_agent="clash.meta",
            ),
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=good)

    refresher = UpstreamRefresher(
        db,
        nodes.cache,
        (source,),
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
        backup=nodes,
    )
    result = await refresher.refresh()
    assert result.updated is True
    assert nodes.is_active() is False
    assert db.runtime_state()["current_digest"] == digest
    assert not stale.exists()


@pytest.fixture
def client(app_settings, tmp_path):
    key = tmp_path / "backup-key"
    key.write_bytes(base64.b64encode(b"k" * 32))
    settings = replace(app_settings, encryption_key_file=key)
    with TestClient(
        create_app(settings, start_scheduler=False),
        client=("127.0.0.1", 50000),
    ) as value:
        value.app.state.services.runtime_settings.update(
            RuntimeSettings(lan_base_url="http://testserver")
        )
        yield value


def login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "initial-user", "password": "initial-password"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _token(client):
    created = client.app.state.services.shares.create("local-test")
    return created.raw_url.rsplit("/", 1)[1]


def _seed_airport(services, payload=b"trojan://air@node.example:443#air\n"):
    digest = services.cache.publish_raw(
        payload, {"subscription-userinfo": "upload=1"}
    )
    services.db.record_refresh_success(
        digest, 1, "uri-list", {"subscription-userinfo": "upload=1"}, 100, "fallback"
    )
    return digest


def test_raw_stays_on_airport_before_threshold(client):
    services = client.app.state.services
    token = _token(client)
    digest = _seed_airport(services)
    services.backup_nodes.save(BACKUP)
    services.db.record_refresh_failure("all_sources_failed", 200)
    response = client.get(f"/raw/{token}")
    assert response.content.startswith(b"trojan://air@")
    assert response.headers["subscription-userinfo"] == "upload=1"
    assert services.db.runtime_state()["current_digest"] == digest


def test_raw_serves_backup_at_threshold_and_keeps_digest(client):
    services = client.app.state.services
    token = _token(client)
    digest = _seed_airport(services)
    services.backup_nodes.save(BACKUP)
    for i in range(3):
        services.db.record_refresh_failure("all_sources_failed", 200 + i)
    response = client.get(f"/raw/{token}")
    assert b"trojan://bak@" in response.content
    assert "subscription-userinfo" not in response.headers
    assert response.headers["profile-update-interval"]
    assert services.db.runtime_state()["current_digest"] == digest


def test_raw_returns_to_airport_after_success(client):
    services = client.app.state.services
    token = _token(client)
    digest = _seed_airport(services)
    services.backup_nodes.save(BACKUP)
    for i in range(3):
        services.db.record_refresh_failure("all_sources_failed", 200 + i)
    assert b"trojan://bak@" in client.get(f"/raw/{token}").content
    services.db.record_refresh_success(
        digest, 1, "uri-list", {"subscription-userinfo": "upload=1"}, 300, "fallback"
    )
    response = client.get(f"/raw/{token}")
    assert response.content.startswith(b"trojan://air@")
    assert services.db.runtime_state()["current_digest"] == digest


def test_invalid_or_empty_backup_never_activates(client):
    services = client.app.state.services
    token = _token(client)
    _seed_airport(services)
    for i in range(3):
        services.db.record_refresh_failure("all_sources_failed", 200 + i)
    assert client.get(f"/raw/{token}").content.startswith(b"trojan://air@")


def test_backup_nodes_api_round_trip_and_overview(client):
    csrf = login(client)
    services = client.app.state.services
    headers = {"X-CSRF-Token": csrf}
    empty = client.get("/api/admin/backup-nodes")
    assert empty.status_code == 200
    assert empty.json()["configured"] is False
    bad = client.put("/api/admin/backup-nodes", headers=headers, json={"nodes": "nope"})
    assert bad.status_code == 400
    saved = client.put("/api/admin/backup-nodes", headers=headers, json={"nodes": BACKUP})
    assert saved.status_code == 200
    assert saved.json()["node_count"] == 2
    assert "trojan://bak@" in client.get("/api/admin/backup-nodes").json()["nodes"]
    overview = client.get("/api/admin/overview").json()
    assert overview["backup_configured"] is True
    assert overview["backup_active"] is False
    assert "trojan://" not in str(overview)
    for i in range(3):
        services.db.record_refresh_failure("all_sources_failed", i)
    assert client.get("/api/admin/overview").json()["backup_active"] is True
    current = client.get("/api/admin/settings").json()
    assert current["backup_fail_threshold"] == 3
    updated = client.put("/api/admin/settings", headers=headers, json={**current, "backup_fail_threshold": 5})
    assert updated.status_code == 200
    assert updated.json()["backup_fail_threshold"] == 5
    cleared = client.put("/api/admin/backup-nodes", headers=headers, json={"nodes": ""})
    assert cleared.status_code == 200
    assert client.get("/api/admin/overview").json()["backup_active"] is False


def test_save_while_already_failing_activates(client):
    csrf = login(client)
    services = client.app.state.services
    token = _token(client)
    _seed_airport(services)
    for i in range(3):
        services.db.record_refresh_failure("all_sources_failed", i)
    client.put(
        "/api/admin/backup-nodes",
        headers={"X-CSRF-Token": csrf},
        json={"nodes": BACKUP},
    )
    assert b"trojan://bak@" in client.get(f"/raw/{token}").content

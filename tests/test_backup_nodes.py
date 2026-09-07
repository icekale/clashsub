import httpx
import pytest
from pydantic import SecretStr

from clashsub.backup_nodes import BackupNodes, parse_backup_nodes
from clashsub.cache_files import CacheFiles
from clashsub.db import Database
from clashsub.secret_store import SecretStore
from clashsub.settings import SettingsStore
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

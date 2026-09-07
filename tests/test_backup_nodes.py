import pytest

from clashsub.backup_nodes import BackupNodes, parse_backup_nodes
from clashsub.cache_files import CacheFiles
from clashsub.db import Database
from clashsub.secret_store import SecretStore
from clashsub.settings import SettingsStore
from clashsub.subscription import InvalidSubscription


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

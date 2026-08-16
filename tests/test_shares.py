import hashlib
from datetime import datetime, timedelta, timezone
import pytest

from clashsub.db import Database
from clashsub.settings import RuntimeSettings, SettingsStore
from clashsub.shares import ShareService


@pytest.fixture
def service(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    SettingsStore(db).update(RuntimeSettings(lan_base_url="http://192.168.1.28:18080"))
    return db, ShareService(db, SettingsStore(db))


def test_create_defaults_to_365_days_and_stores_only_hash(service):
    db, shares = service
    before = datetime.now(timezone.utc)
    created = shares.create("friend-a")
    assert created.raw_url.startswith("http://192.168.1.28:18080/raw/")
    token = created.raw_url.rsplit("/", 1)[1]
    assert created.clash_url is None
    row = db.find_share_by_hash(hashlib.sha256(token.encode()).hexdigest())
    assert row["token_hash"] != token
    assert before + timedelta(days=364) < datetime.fromtimestamp(row["expires_at"], timezone.utc)
    assert token not in str(shares.list())


def test_clash_permission_implies_raw_and_token_is_one_time(service):
    db, shares = service
    created = shares.create("friend-b", allow_raw=False, allow_clash=True)
    row = db.get_share(created.id)
    assert row["allow_raw"] == 1 and row["allow_clash"] == 1
    assert created.clash_url is not None
    token = created.raw_url.rsplit("/", 1)[1]
    assert created.clash_ha_url == f"http://192.168.1.28:18080/clash-ha/{token}"
    assert not hasattr(shares.list()[0], "token")


def test_public_converted_share_keeps_format_urls_on_its_own_origin(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    settings = SettingsStore(db)
    settings.update(
        RuntimeSettings(
            access_mode="public",
            public_base_url="https://share.example.test",
            converter_enabled=True,
        )
    )
    shares = ShareService(db, settings)

    created = shares.create("friend", allow_clash=True)

    token = created.raw_url.rsplit("/", 1)[1]
    for kind, url in (
        ("clash", created.clash_url),
        ("clash-ha", created.clash_ha_url),
        ("surge", created.surge_url),
        ("loon", created.loon_url),
    ):
        assert url == f"https://share.example.test/{kind}/{token}"
        assert "converter.example.test" not in url
        assert "url=" not in url


def test_revoke_renew_rotate_and_delete(service):
    db, shares = service
    created = shares.create("friend-c", days=7)
    old_token = created.raw_url.rsplit("/", 1)[1]
    shares.renew(created.id, 30)
    shares.revoke(created.id)
    assert shares.resolve(old_token) is None
    with pytest.raises(KeyError, match="share not found"):
        shares.rotate(created.id)
    shares.delete(created.id)
    assert shares.resolve(old_token) is None


def test_expired_share_is_rejected_without_affecting_another_share(service):
    db, shares = service
    expired = shares.create("short", days=1)
    healthy = shares.create("long", days=30)
    expired_token = expired.raw_url.rsplit("/", 1)[1]
    healthy_token = healthy.raw_url.rsplit("/", 1)[1]
    assert shares.resolve(expired_token, now=expired.expires_at + 1) is None
    assert shares.resolve(healthy_token, now=expired.expires_at + 1) is not None


def test_renew_rejects_a_revoked_share(service):
    db, shares = service
    created = shares.create("revoked", days=7)
    shares.revoke(created.id)

    with pytest.raises(KeyError, match="share not found"):
        shares.renew(created.id, 30)


def test_create_without_active_base_url_writes_no_share(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    shares = ShareService(db, SettingsStore(db))

    with pytest.raises(ValueError, match="active base URL"):
        shares.create("friend")

    assert db.list_shares() == []


def test_expired_share_cannot_rotate(service):
    db, shares = service
    created = shares.create("expired")
    db.update_share_expiry(created.id, 0)

    with pytest.raises(KeyError, match="share not found"):
        shares.rotate(created.id)

import base64
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from clashsub.app import create_app
from clashsub.cache_files import CacheFiles
from clashsub.db import Database
from clashsub.secret_store import SecretStore
from clashsub.settings import RuntimeSettings, SettingsStore
from clashsub.shares import ShareService


def _key_file(tmp_path):
    path = tmp_path / "key"
    path.write_bytes(base64.b64encode(b"k" * 32))
    return path


def _service(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    settings = SettingsStore(db)
    settings.update(RuntimeSettings(lan_base_url="https://share.example.test"))
    return db, ShareService(db, settings, SecretStore(db, _key_file(tmp_path)))


def test_created_share_persists_ciphertext_origin_and_reveals_same_url(tmp_path):
    db, shares = _service(tmp_path)
    created = shares.create("friend")

    row = db.get_share(created.id)
    assert row["token_ciphertext"]
    assert row["token_nonce"]
    assert row["base_url"] == "https://share.example.test"
    assert shares.list()[0].recoverable is True
    assert shares.reveal(created.id, "raw") == created.raw_url
    assert shares.reveal(created.id, "raw") == created.raw_url


def test_rotate_replaces_recoverable_token_without_changing_origin(tmp_path):
    db, shares = _service(tmp_path)
    created = shares.create("friend", allow_clash=True)
    rotated = shares.rotate(created.id)

    assert rotated.raw_url != created.raw_url
    assert rotated.raw_url == shares.reveal(created.id, "raw")
    assert rotated.clash_url == shares.reveal(created.id, "clash")
    assert db.get_share(created.id)["base_url"] == "https://share.example.test"


def test_rotate_refreshes_stored_base_url_after_origin_change(tmp_path):
    db, shares = _service(tmp_path)
    created = shares.create("friend")
    shares.settings.update(RuntimeSettings(lan_base_url="https://new.example.test"))

    rotated = shares.rotate(created.id)

    # 轮换后的恢复链接必须使用当前 base URL，而不是创建时的旧 origin。
    assert rotated.raw_url.startswith("https://new.example.test/raw/")
    assert shares.reveal(created.id, "raw") == rotated.raw_url
    assert db.get_share(created.id)["base_url"] == "https://new.example.test"


def test_rotate_raises_value_error_when_no_active_base_url(tmp_path):
    db, shares = _service(tmp_path)
    created = shares.create("friend")
    shares.settings.update(RuntimeSettings(lan_base_url=""))

    with pytest.raises(ValueError):
        shares.rotate(created.id)


def test_admin_list_carries_existing_urls_without_reveal_posts(app_settings, tmp_path):
    """列表接口直接带回已有链接，页面显示不再依赖逐个 reveal 请求。"""
    settings = replace(app_settings, encryption_key_file=_key_file(tmp_path))
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "initial-user", "password": "initial-password"},
        )
        csrf = login.json()["csrf_token"]
        client.app.state.services.runtime_settings.update(
            RuntimeSettings(lan_base_url="https://share.example.test")
        )
        created = client.post(
            "/api/admin/shares",
            headers={"X-CSRF-Token": csrf},
            json={"label": "friend", "allow_clash": True},
        ).json()

        listed = client.get("/api/admin/shares").json()
        assert listed[0]["urls"] == {
            "raw": created["raw_url"],
            "clash": created["clash_url"],
            "surge": created["surge_url"],
            "loon": created["loon_url"],
            "smart": created["smart_url"],
        }

        # 撤销后不再携带 urls。
        assert (
            client.post(
                f"/api/admin/shares/{created['id']}/revoke",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 204
        )
        assert "urls" not in client.get("/api/admin/shares").json()[0]


def test_reveal_endpoint_is_csrf_protected_and_reusable(app_settings, tmp_path):
    settings = replace(app_settings, encryption_key_file=_key_file(tmp_path))
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "initial-user", "password": "initial-password"},
        )
        csrf = login.json()["csrf_token"]
        client.app.state.services.runtime_settings.update(
            RuntimeSettings(lan_base_url="https://share.example.test")
        )
        created = client.post(
            "/api/admin/shares",
            headers={"X-CSRF-Token": csrf},
            json={"label": "friend", "allow_clash": True},
        ).json()

        assert client.post(f"/api/admin/shares/{created['id']}/reveal", json={"kind": "raw"}).status_code == 403
        response = client.post(
            f"/api/admin/shares/{created['id']}/reveal",
            headers={"X-CSRF-Token": csrf},
            json={"kind": "raw"},
        )
        again = client.post(
            f"/api/admin/shares/{created['id']}/reveal",
            headers={"X-CSRF-Token": csrf},
            json={"kind": "raw"},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["url"] == created["raw_url"]
    assert again.json() == response.json()

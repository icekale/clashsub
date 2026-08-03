import base64

import pytest

from clashsub.db import Database
from clashsub.migrate import import_link
from clashsub.secret_store import SecretStore
from clashsub.settings import RuntimeSettings, SettingsStore
from clashsub.shares import ShareService


def _key_file(tmp_path):
    path = tmp_path / "key"
    path.write_bytes(base64.b64encode(b"k" * 32))
    return path


def _fixture(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    settings = SettingsStore(db)
    settings.update(RuntimeSettings(lan_base_url="http://lan.example.test:18080"))
    shares = ShareService(db, settings)
    created = shares.create("legacy")
    return db, SecretStore(db, _key_file(tmp_path)), created


def test_import_matches_hash_and_preserves_origin(tmp_path):
    db, store, created = _fixture(tmp_path)
    token = created.raw_url.rsplit("/", 1)[1]

    result = import_link(store, db, f"https://public.example.test/raw/{token}")

    assert result == "imported"
    row = db.get_share(created.id)
    assert row["base_url"] == "https://public.example.test"
    assert row["token_ciphertext"]
    assert store.open(f"share:{created.id}", row["token_version"], row["token_nonce"], row["token_ciphertext"]) == token


def test_import_is_idempotent_and_refuses_wrong_route(tmp_path):
    db, store, created = _fixture(tmp_path)
    token = created.raw_url.rsplit("/", 1)[1]

    assert import_link(store, db, f"https://public.example.test/raw/{token}") == "imported"
    row = db.get_share(created.id)
    ciphertext = row["token_ciphertext"]
    assert import_link(store, db, f"https://other.example.test/raw/{token}") == "already_recoverable"
    assert db.get_share(created.id)["token_ciphertext"] == ciphertext
    with pytest.raises(ValueError, match="route permission"):
        import_link(store, db, f"https://public.example.test/clash/{token}")


def test_import_rejects_unknown_or_unsafe_links(tmp_path):
    db, store, _ = _fixture(tmp_path)
    with pytest.raises(ValueError, match="invalid link"):
        import_link(store, db, "https://public.example.test/not-a-share/token")

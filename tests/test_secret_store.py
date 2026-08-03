import base64

import pytest

from clashsub.db import Database
from clashsub.secret_store import SecretStore, SecretStoreUnavailable


def _key_file(tmp_path, value=b"k" * 32):
    path = tmp_path / "key"
    path.write_text(base64.b64encode(value).decode(), encoding="ascii")
    return path


def test_secret_store_round_trip_uses_distinct_nonces(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    store = SecretStore(db, _key_file(tmp_path))
    store.put("airport", "member@example.test\nairport-pass")
    first = db.get_encrypted_secret("airport")

    store.put("airport", "member@example.test\nairport-pass")
    second = db.get_encrypted_secret("airport")

    assert first["nonce"] != second["nonce"]
    assert store.get("airport") == "member@example.test\nairport-pass"


def test_secret_store_rejects_wrong_key_without_plaintext(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    SecretStore(db, _key_file(tmp_path, b"a" * 32)).put("airport", "private-value")

    with pytest.raises(SecretStoreUnavailable, match="unavailable"):
        SecretStore(db, _key_file(tmp_path, b"b" * 32)).get("airport")

    with db.connect() as conn:
        assert "private-value" not in repr(conn.execute("SELECT * FROM encrypted_secrets").fetchall())


def test_secret_store_rejects_malformed_key(tmp_path):
    key = tmp_path / "bad-key"
    key.write_text(base64.b64encode(b"short").decode(), encoding="ascii")
    db = Database(tmp_path / "state.db")
    db.initialize()

    with pytest.raises(SecretStoreUnavailable, match="unavailable"):
        SecretStore(db, key).put("airport", "value")


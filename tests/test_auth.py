import hashlib
import time

from clashsub.auth import AuthService
from clashsub.db import Database


def test_bootstrap_hashes_password_and_login_creates_server_session(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    auth = AuthService(db)
    auth.bootstrap("initial-user", "initial-password")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM admins WHERE id=1").fetchone()
        assert row["password_hash"] != "initial-password"
    login = auth.login("initial-user", "initial-password", now=100)
    assert login.session_token != login.csrf_token
    assert db.get_session(hashlib.sha256(login.session_token.encode()).hexdigest()) is not None
    assert auth.authenticate(login.session_token, login.csrf_token, now=101) is not None


def test_csrf_mismatch_and_credential_change_invalidate_sessions(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    auth = AuthService(db)
    auth.bootstrap("first-user", "first-password")
    login = auth.login("first-user", "first-password", now=100)
    assert auth.authenticate(login.session_token, "wrong", now=101, require_csrf=True) is None
    auth.change_credentials(
        login.session_token,
        login.csrf_token,
        "first-password",
        "second-user",
        "second-password",
        now=102,
    )
    assert auth.authenticate(login.session_token, login.csrf_token, now=103) is None
    assert auth.login("second-user", "second-password", now=104)

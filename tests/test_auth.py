import hashlib
import secrets
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


def test_csrf_for_session_is_stable_across_restores(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    auth = AuthService(db)
    auth.bootstrap("initial-user", "initial-password")
    login = auth.login("initial-user", "initial-password", now=100)
    first = auth.csrf_for_session(login.session_token, now=101)
    second = auth.csrf_for_session(login.session_token, now=102)
    assert first == second == login.csrf_token
    # The stable token keeps authorizing mutations like a second tab's reveal.
    assert auth.authenticate(login.session_token, second, now=103, require_csrf=True) is not None


def test_legacy_session_without_raw_token_gets_one_on_first_restore(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    auth = AuthService(db)
    auth.bootstrap("initial-user", "initial-password")
    session = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash, csrf_hash, admin_id, expires_at, created_at) VALUES (?, ?, 1, ?, ?)",
            (hashlib.sha256(session.encode()).hexdigest(), hashlib.sha256(csrf.encode()).hexdigest(), 200, 100),
        )
    # A legacy row has no raw csrf_token; first restore materializes one.
    issued = auth.csrf_for_session(session, now=101)
    assert issued != csrf
    assert auth.authenticate(session, issued, now=102, require_csrf=True) is not None
    # The materialized token is now stable for subsequent restores.
    assert auth.csrf_for_session(session, now=103) == issued
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE token_hash=?", (hashlib.sha256(session.encode()).hexdigest(),)).fetchone()
    assert row["csrf_token"] == issued
    assert row["csrf_hash"] == hashlib.sha256(issued.encode()).hexdigest()

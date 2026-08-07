from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from .db import Database


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LoginResult:
    session_token: str
    csrf_token: str
    expires_at: float


class AuthService:
    SESSION_SECONDS = 24 * 3600

    def __init__(self, db: Database):
        self.db, self.hasher = db, PasswordHasher()
        self._dummy_hash = None

    def _dummy_verify(self, password: str) -> None:
        """Run an argon2 verification against a precomputed hash.

        Keeps login timing roughly constant for unknown usernames so a remote
        attacker cannot confirm which usernames exist via response time.
        """
        if self._dummy_hash is None:
            self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(24))
        try:
            self.hasher.verify(self._dummy_hash, password)
        except VerificationError:
            pass

    def bootstrap(self, username: str, password: str):
        if not username.strip() or not password:
            raise ValueError("initial credentials are required")
        if self.db.get_admin() is not None:
            return  # 管理员已存在，argon2 哈希只计算一次，避免每次启动重复开销。
        self.db.bootstrap_admin(username.strip(), self.hasher.hash(password), time.time())

    def login(self, username: str, password: str, now: float) -> LoginResult:
        admin = self.db.get_admin_by_username(username)
        if not admin:
            self._dummy_verify(password)
            raise PermissionError("invalid credentials")
        try:
            self.hasher.verify(admin["password_hash"], password)
        except VerificationError as exc:
            raise PermissionError("invalid credentials") from exc
        session = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        expires = now + self.SESSION_SECONDS
        self.db.insert_session(digest(session), digest(csrf), csrf, expires, now)
        return LoginResult(session, csrf, expires)

    def authenticate(
        self,
        session: str,
        csrf: str | None,
        now: float,
        require_csrf: bool = False,
    ):
        row = self.db.get_session(digest(session)) if session else None
        if not row:
            return None
        if row["expires_at"] <= now:
            self.db.delete_session(row["token_hash"])
            return None
        if require_csrf and (not csrf or not secrets.compare_digest(row["csrf_hash"], digest(csrf))):
            return None
        return row

    def logout(self, session_token: str) -> None:
        if session_token:
            self.db.delete_session(digest(session_token))

    def change_credentials(
        self,
        session_token: str,
        csrf_token: str,
        current_password: str,
        new_username: str,
        new_password: str,
        now: float,
    ) -> None:
        if not self.authenticate(session_token, csrf_token, now, require_csrf=True):
            raise PermissionError("invalid session")
        admin = self.db.get_admin()
        try:
            self.hasher.verify(admin["password_hash"], current_password)
        except VerificationError as exc:
            raise PermissionError("invalid credentials") from exc
        if not new_username.strip() or not new_password:
            raise ValueError("new credentials are required")
        self.db.update_admin_and_delete_sessions(
            new_username.strip(),
            self.hasher.hash(new_password),
            now,
        )

    def csrf_for_session(self, session_token: str, now: float | None = None) -> str:
        """Return the session's CSRF token without rotating it.

        The token is stable for the life of the session so that multiple pages or
        tabs sharing the same session cookie all remain usable. Legacy sessions
        created before raw token storage get a token generated on first restore.
        """
        current = time.time() if now is None else now
        row = self.db.get_session(digest(session_token))
        if not row:
            raise PermissionError("invalid session")
        if row["expires_at"] <= current:
            self.db.delete_session(row["token_hash"])
            raise PermissionError("invalid session")
        if row["csrf_token"]:
            return row["csrf_token"]
        csrf = secrets.token_urlsafe(32)
        self.db.set_session_csrf_token(row["token_hash"], digest(csrf), csrf)
        return csrf

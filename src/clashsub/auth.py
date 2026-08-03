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

    def bootstrap(self, username: str, password: str):
        if not username.strip() or not password:
            raise ValueError("initial credentials are required")
        self.db.bootstrap_admin(username.strip(), self.hasher.hash(password), time.time())

    def login(self, username: str, password: str, now: float) -> LoginResult:
        admin = self.db.get_admin_by_username(username)
        if not admin:
            raise PermissionError("invalid credentials")
        try:
            self.hasher.verify(admin["password_hash"], password)
        except VerificationError as exc:
            raise PermissionError("invalid credentials") from exc
        session = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        expires = now + self.SESSION_SECONDS
        self.db.insert_session(digest(session), digest(csrf), expires, now)
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

    def rotate_csrf(self, session_token: str, now: float | None = None) -> str:
        current = time.time() if now is None else now
        if not self.authenticate(session_token, None, current):
            raise PermissionError("invalid session")
        csrf = secrets.token_urlsafe(32)
        self.db.update_session_csrf(digest(session_token), digest(csrf))
        return csrf

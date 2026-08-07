from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass

from .db import Database
from .secret_store import SecretStore, SecretStoreUnavailable
from .settings import SettingsStore


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CreatedShare:
    id: str
    raw_url: str
    clash_url: str | None
    surge_url: str | None
    loon_url: str | None
    smart_url: str | None
    expires_at: float


@dataclass(frozen=True)
class ShareSummary:
    id: str
    label: str
    expires_at: float
    allow_raw: bool
    allow_clash: bool
    revoked: bool
    last_access_at: float | None
    access_count: int
    recoverable: bool


class ShareService:
    def __init__(
        self,
        db: Database,
        settings: SettingsStore,
        secret_store: SecretStore | None = None,
    ):
        self.db, self.settings, self.secret_store = db, settings, secret_store

    def _format_url(self, base_url: str, token: str, kind: str) -> str:
        return f"{base_url.rstrip('/')}/{kind}/{token}"

    def _urls(self, share_id: str, token: str, expires_at: float, allow_clash: bool) -> CreatedShare:
        base = self.settings.get().active_base_url()
        if not base:
            raise ValueError("active base URL is required before creating a share")
        return CreatedShare(
            share_id,
            f"{base}/raw/{token}",
            self._format_url(base, token, "clash") if allow_clash else None,
            self._format_url(base, token, "surge") if allow_clash else None,
            self._format_url(base, token, "loon") if allow_clash else None,
            f"{base}/smart/{token}" if allow_clash else None,
            expires_at,
        )

    def create(
        self,
        label: str,
        days: int = 365,
        allow_raw: bool = True,
        allow_clash: bool = False,
    ) -> CreatedShare:
        if not label.strip() or not 1 <= days <= 3650:
            raise ValueError("label and lifetime are invalid")
        allow_raw = bool(allow_raw or allow_clash)
        token, share_id, now = secrets.token_urlsafe(32), str(uuid.uuid4()), time.time()
        expires_at = now + days * 86400
        base = self.settings.get().active_base_url()
        if not base:
            raise ValueError("active base URL is required before creating a share")
        sealed = (None, None, None)
        if self.secret_store is not None and self.secret_store.available:
            sealed = self.secret_store.seal(f"share:{share_id}", token)
        self.db.insert_share(
            share_id,
            label.strip(),
            token_hash(token),
            now,
            expires_at,
            allow_raw,
            allow_clash,
            *sealed,
            base_url=base,
        )
        return self._urls(share_id, token, expires_at, allow_clash)

    def resolve(self, token: str, require_clash: bool = False, now: float | None = None):
        row = self.db.find_share_by_hash(token_hash(token))
        current = time.time() if now is None else now
        if not row or row["revoked_at"] is not None or row["expires_at"] <= current:
            return None
        if not row["allow_raw"] or (require_clash and not row["allow_clash"]):
            return None
        self.db.record_share_access(row["id"], current)
        return row

    def list(self) -> list[ShareSummary]:
        return [
            ShareSummary(
                row["id"],
                row["label"],
                row["expires_at"],
                bool(row["allow_raw"]),
                bool(row["allow_clash"]),
                row["revoked_at"] is not None,
                row["last_access_at"],
                row["access_count"],
                bool(row["token_ciphertext"] and row["token_nonce"] and row["base_url"]),
            )
            for row in self.db.list_shares()
        ]

    def renew(self, share_id: str, days: int) -> None:
        if not 1 <= days <= 3650:
            raise ValueError("lifetime is invalid")
        row = self.db.get_share(share_id)
        if not row or row["revoked_at"] is not None:
            raise KeyError("share not found")
        self.db.update_share_expiry(share_id, time.time() + days * 86400)

    def revoke(self, share_id: str) -> None:
        if not self.db.get_share(share_id):
            raise KeyError("share not found")
        self.db.revoke_share(share_id, time.time())

    def rotate(self, share_id: str) -> CreatedShare:
        row = self.db.get_share(share_id)
        if not row or row["revoked_at"] is not None or row["expires_at"] <= time.time():
            raise KeyError("share not found")
        base = self.settings.get().active_base_url()
        if not base:
            raise ValueError("active base URL is required before rotating a share")
        token = secrets.token_urlsafe(32)
        sealed = (None, None, None)
        if self.secret_store is not None and self.secret_store.available:
            sealed = self.secret_store.seal(f"share:{share_id}", token)
        self.db.rotate_share(share_id, token_hash(token), *sealed, base_url=base)
        return self._urls(share_id, token, row["expires_at"], bool(row["allow_clash"]))

    def reveal(self, share_id: str, kind: str) -> str:
        row = self.db.get_share(share_id)
        if not row or row["revoked_at"] is not None or row["expires_at"] <= time.time():
            raise KeyError("share not found")
        if kind not in {"raw", "clash", "surge", "loon", "smart"} or (kind == "raw" and not row["allow_raw"]) or (
            kind in {"clash", "surge", "loon", "smart"} and not row["allow_clash"]
        ):
            raise KeyError("share route unavailable")
        if not row["token_ciphertext"] or not row["token_nonce"] or not row["base_url"] or self.secret_store is None:
            raise KeyError("share is not recoverable")
        try:
            token = self.secret_store.open(
                f"share:{share_id}",
                row["token_version"],
                row["token_nonce"],
                row["token_ciphertext"],
            )
        except SecretStoreUnavailable as exc:
            raise RuntimeError("share recovery unavailable") from exc
        return self._format_url(row["base_url"], token, kind)

    def delete(self, share_id: str) -> None:
        if not self.db.get_share(share_id):
            raise KeyError("share not found")
        self.db.delete_share(share_id)

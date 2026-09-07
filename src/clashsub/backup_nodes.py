from __future__ import annotations

from .cache_files import CacheFiles, RawSnapshot
from .db import Database
from .secret_store import SecretStore, SecretStoreUnavailable
from .settings import SettingsStore
from .subscription import InvalidSubscription, ValidatedSubscription, validate_subscription

SECRET_NAME = "backup_nodes"


def normalize_nodes(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def parse_backup_nodes(text: str, max_bytes: int = 8 * 1024 * 1024) -> ValidatedSubscription:
    normalized = normalize_nodes(text)
    if not normalized:
        raise InvalidSubscription("backup nodes are empty")
    return validate_subscription(normalized.encode("utf-8"), max_bytes)


class BackupNodes:
    def __init__(
        self,
        db: Database,
        cache: CacheFiles,
        store: SecretStore,
        settings_store: SettingsStore,
        max_bytes: int = 8 * 1024 * 1024,
    ):
        self.db = db
        self.cache = cache
        self.store = store
        self.settings_store = settings_store
        self.max_bytes = max_bytes

    def load(self) -> ValidatedSubscription | None:
        if not self.store.available:
            return None
        try:
            raw = self.store.get(SECRET_NAME)
        except SecretStoreUnavailable:
            return None
        if not raw:
            return None
        try:
            return parse_backup_nodes(raw, self.max_bytes)
        except InvalidSubscription:
            return None

    def is_active(self, state=None) -> bool:
        state = self.db.runtime_state() if state is None else state
        failures = state["consecutive_failures"] or 0
        if failures < self.settings_store.get().backup_fail_threshold:
            return False
        return self.load() is not None

    def snapshot(self) -> RawSnapshot | None:
        parsed = self.load()
        if parsed is None:
            return None
        return RawSnapshot(parsed.payload, {})

    def status(self) -> dict:
        available = self.store.available
        nodes = ""
        count = 0
        configured = False
        if available:
            try:
                raw = self.store.get(SECRET_NAME) or ""
                nodes = raw
                if raw.strip():
                    parsed = self.load()
                    configured = parsed is not None
                    count = parsed.node_count if parsed else 0
            except SecretStoreUnavailable:
                available = False
        return {
            "configured": configured,
            "node_count": count,
            "nodes": nodes,
            "management_available": available,
        }

    def save(self, text: str) -> dict:
        was_active = self.is_active()
        if not normalize_nodes(text):
            if not self.store.available:
                raise SecretStoreUnavailable("encrypted secret store unavailable")
            self.store.delete(SECRET_NAME)
        else:
            parsed = parse_backup_nodes(text, self.max_bytes)
            self.store.put(SECRET_NAME, parsed.payload.decode("utf-8"))
        now_active = self.is_active()
        if was_active != now_active:
            self.cache.clear_converted()
        result = self.status()
        result["should_notify"] = was_active != now_active
        return result

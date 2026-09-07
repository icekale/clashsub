from __future__ import annotations

from urllib.parse import unquote, urlparse

import yaml

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
    if normalized:
        try:
            parsed = validate_subscription(normalized.encode("utf-8"), max_bytes)
            if parsed.content_format == "uri-list":
                return parsed
        except InvalidSubscription:
            pass
    if not text.strip():
        raise InvalidSubscription("backup nodes are empty")
    return validate_subscription(text.encode("utf-8"), max_bytes)


def backup_proxies(payload: bytes) -> list[dict]:
    text = payload.decode("utf-8")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        document = None
    if isinstance(document, dict) and isinstance(document.get("proxies"), list):
        return [proxy for proxy in document["proxies"] if isinstance(proxy, dict)]
    proxies = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = urlparse(line)
        host, port = parsed.hostname, parsed.port
        if not host or not port:
            continue
        proxies.append({
            "name": unquote(parsed.fragment) if parsed.fragment else host,
            "type": parsed.scheme,
            "server": host,
            "port": port,
        })
    return proxies


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

    def proxies(self) -> list[dict]:
        parsed = self.load()
        if parsed is None:
            return []
        return backup_proxies(parsed.payload)

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

    def served_snapshot(self) -> RawSnapshot:
        if self.is_active():
            snap = self.snapshot()
            if snap is not None:
                return snap
        digest = self.db.runtime_state()["current_digest"]
        if not digest:
            raise FileNotFoundError("subscription cache unavailable")
        return self.cache.read_raw(digest)

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

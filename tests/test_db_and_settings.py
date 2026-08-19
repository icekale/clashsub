from pathlib import Path

import pytest

from clashsub.db import Database
from clashsub.settings import RuntimeSettings, SettingsStore


def test_database_enables_wal_and_bootstraps_defaults(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

    store = SettingsStore(db)
    assert store.get() == RuntimeSettings(
        refresh_interval_minutes=60,
        access_mode="lan",
        lan_base_url="",
        public_base_url="",
        converter_enabled=False,
    )


def test_public_mode_requires_https_base_url(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    store = SettingsStore(db)
    with pytest.raises(ValueError, match="HTTPS"):
        store.update(RuntimeSettings(10, "public", "http://nas:18080", "http://sub.example.com", False))


@pytest.mark.parametrize(
    "value",
    [
        "http://user:pass@nas:18080",
        "http://nas:0/path",
        "http://nas:18080/path?x=1",
        "http://nas:18080/path#frag",
        "http://nas:\t18080",
        "http://[bad/path",
    ],
)
def test_lan_base_url_rejects_unsafe_origins(value):
    with pytest.raises(ValueError, match="LAN base URL"):
        RuntimeSettings(lan_base_url=value).validated()


def test_public_base_url_rejects_credentials_and_query():
    with pytest.raises(ValueError, match="public base URL"):
        RuntimeSettings(
            access_mode="public",
            public_base_url="https://user:pass@sub.example.com/?q=1",
        ).validated()


def test_integration_settings_defaults(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    store = SettingsStore(db)
    settings = store.get()
    assert settings.openclash_enabled is False
    assert settings.openclash_api_url == ""
    assert settings.openclash_provider == ""
    assert settings.health_enabled is False
    assert settings.health_interval_seconds == 600
    assert settings.health_timeout_seconds == 5


def test_openclash_enabled_requires_url_and_provider():
    with pytest.raises(ValueError, match="OpenClash API URL"):
        RuntimeSettings(openclash_enabled=True).validated()
    with pytest.raises(ValueError, match="provider"):
        RuntimeSettings(
            openclash_enabled=True,
            openclash_api_url="http://192.168.1.1:9090",
        ).validated()
    settings = RuntimeSettings(
        openclash_enabled=True,
        openclash_api_url="http://192.168.1.1:9090/",
        openclash_provider="Provider_988009",
    ).validated()
    assert settings.openclash_api_url == "http://192.168.1.1:9090"


def test_openclash_url_rejects_unsafe_origins():
    with pytest.raises(ValueError, match="OpenClash API URL"):
        RuntimeSettings(
            openclash_enabled=True,
            openclash_api_url="http://user:pass@192.168.1.1:9090",
            openclash_provider="Provider_988009",
        ).validated()


def test_health_settings_validation():
    with pytest.raises(ValueError, match="health check interval"):
        RuntimeSettings(health_interval_seconds=29).validated()
    with pytest.raises(ValueError, match="health check interval"):
        RuntimeSettings(health_interval_seconds=86401).validated()
    with pytest.raises(ValueError, match="health check timeout"):
        RuntimeSettings(health_timeout_seconds=0).validated()
    with pytest.raises(ValueError, match="health check timeout"):
        RuntimeSettings(health_timeout_seconds=31).validated()


def test_night_health_settings_validation():
    with pytest.raises(ValueError, match="night health interval"):
        RuntimeSettings(health_night_enabled=True, health_night_interval_seconds=29).validated()
    with pytest.raises(ValueError, match="night window hours"):
        RuntimeSettings(health_night_enabled=True, health_night_start_hour=24).validated()
    with pytest.raises(ValueError, match="night window start and end hours must differ"):
        RuntimeSettings(
            health_night_enabled=True,
            health_night_start_hour=0,
            health_night_end_hour=0,
        ).validated()


def test_effective_health_interval_uses_night_window():
    settings = RuntimeSettings(
        health_interval_seconds=60,
        health_night_enabled=True,
        health_night_interval_seconds=600,
        health_night_start_hour=0,
        health_night_end_hour=8,
    )
    assert settings.effective_health_interval(0) == 600
    assert settings.effective_health_interval(3) == 600
    assert settings.effective_health_interval(8) == 60
    assert settings.effective_health_interval(23) == 60


def test_effective_health_interval_supports_wraparound_window():
    settings = RuntimeSettings(
        health_interval_seconds=60,
        health_night_enabled=True,
        health_night_interval_seconds=300,
        health_night_start_hour=22,
        health_night_end_hour=6,
    )
    assert settings.effective_health_interval(23) == 300
    assert settings.effective_health_interval(0) == 300
    assert settings.effective_health_interval(5) == 300
    assert settings.effective_health_interval(6) == 60
    assert settings.effective_health_interval(12) == 60


def test_effective_health_interval_ignored_when_disabled():
    settings = RuntimeSettings(
        health_interval_seconds=60,
        health_night_enabled=False,
        health_night_interval_seconds=600,
    )
    assert settings.effective_health_interval(3) == 60


def test_health_refresh_settings_defaults(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    settings = SettingsStore(db).get()
    assert settings.health_refresh_enabled is False
    assert settings.health_refresh_online_ratio == 0.5
    assert settings.health_refresh_cooldown_minutes == 10


def test_health_refresh_settings_validation():
    with pytest.raises(ValueError, match="online ratio"):
        RuntimeSettings(health_refresh_online_ratio=0).validated()
    with pytest.raises(ValueError, match="online ratio"):
        RuntimeSettings(health_refresh_online_ratio=1.1).validated()
    with pytest.raises(ValueError, match="cooldown"):
        RuntimeSettings(health_refresh_cooldown_minutes=0).validated()


def test_settings_store_round_trip_health_refresh(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            health_refresh_enabled=True,
            health_refresh_online_ratio=0.6,
            health_refresh_cooldown_minutes=15,
        )
    )
    settings = store.get()
    assert settings.health_refresh_enabled is True
    assert settings.health_refresh_online_ratio == 0.6
    assert settings.health_refresh_cooldown_minutes == 15


def test_integration_settings_persist_roundtrip(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    store = SettingsStore(db)
    store.update(
        RuntimeSettings(
            openclash_enabled=True,
            openclash_api_url="http://192.168.1.1:9090",
            openclash_provider="Provider_988009",
            health_enabled=True,
            health_interval_seconds=900,
            health_timeout_seconds=8,
            health_night_enabled=True,
            health_night_interval_seconds=600,
            health_night_start_hour=22,
            health_night_end_hour=6,
        )
    )
    loaded = store.get()
    assert loaded.openclash_enabled is True
    assert loaded.openclash_provider == "Provider_988009"
    assert loaded.health_interval_seconds == 900
    assert loaded.health_timeout_seconds == 8
    assert loaded.health_night_enabled is True
    assert loaded.health_night_interval_seconds == 600
    assert loaded.health_night_start_hour == 22
    assert loaded.health_night_end_hour == 6


def test_node_health_roundtrip(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    now = 1000.0
    db.replace_node_health(
        [("Node A", 1, 152.5, now), ("Node B", 0, None, now)]
    )
    rows = db.list_node_health()
    assert [(row["name"], row["ok"], row["latency_ms"], row["checked_at"]) for row in rows] == [
        ("Node A", 1, 152.5, now),
        ("Node B", 0, None, now),
    ]
    db.replace_node_health([("Node C", 1, 10.0, now + 1)])
    rows = db.list_node_health()
    assert [row["name"] for row in rows] == ["Node C"]


def test_runtime_state_migrates_in_place(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE runtime_state (
              id INTEGER PRIMARY KEY CHECK (id = 1), current_digest TEXT,
              node_count INTEGER NOT NULL DEFAULT 0, content_format TEXT,
              safe_headers_json TEXT NOT NULL DEFAULT '{}', last_attempt_at REAL,
              last_success_at REAL, consecutive_failures INTEGER NOT NULL DEFAULT 0,
              last_error TEXT
            );
            INSERT INTO runtime_state(id, current_digest, node_count)
            VALUES (1, 'old-digest', 7);
            """
        )

    db.initialize()
    state = db.runtime_state()

    assert state["current_digest"] == "old-digest"
    assert state["node_count"] == 7
    assert state["last_success_source"] is None
    assert state["protocol_last_login_at"] is None
    assert state["protocol_last_subscribe_at"] is None
    assert state["protocol_subscription_expires_at"] is None
    assert state["protocol_last_error_category"] is None


def test_secret_and_share_recovery_columns_migrate_in_place(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE shares (
              id TEXT PRIMARY KEY, label TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
              created_at REAL NOT NULL, expires_at REAL NOT NULL,
              allow_raw INTEGER NOT NULL, allow_clash INTEGER NOT NULL,
              revoked_at REAL, last_access_at REAL, access_count INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO shares(id, label, token_hash, created_at, expires_at, allow_raw, allow_clash)
            VALUES ('share-1', 'legacy', 'hash', 1, 2, 1, 0);
            """
        )

    db.initialize()

    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(shares)")}
        assert {"token_version", "token_nonce", "token_ciphertext", "base_url"} <= columns
        assert conn.execute("SELECT label FROM shares WHERE id='share-1'").fetchone()[0] == "legacy"
        assert conn.execute("SELECT name FROM encrypted_secrets").fetchall() == []


def test_legacy_sessions_gain_csrf_token_column_in_place(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE sessions (
              token_hash TEXT PRIMARY KEY,
              csrf_hash TEXT NOT NULL,
              admin_id INTEGER NOT NULL,
              expires_at REAL NOT NULL,
              created_at REAL NOT NULL
            );
            INSERT INTO sessions(token_hash, csrf_hash, admin_id, expires_at, created_at)
            VALUES ('legacy-hash', 'csrf-hash', 1, 2, 1);
            """
        )

    db.initialize()

    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        assert "csrf_token" in columns
        row = conn.execute("SELECT * FROM sessions WHERE token_hash='legacy-hash'").fetchone()
        assert row["csrf_token"] is None


def test_protocol_status_success_and_failure_are_recorded(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    db.record_protocol_failure("challenge")
    assert db.runtime_state()["protocol_last_error_category"] == "challenge"

    db.record_protocol_success(10, 11, 1_900_000_000)
    state = db.runtime_state()

    assert state["protocol_last_login_at"] == 10
    assert state["protocol_last_subscribe_at"] == 11
    assert state["protocol_subscription_expires_at"] == 1_900_000_000
    assert state["protocol_last_error_category"] is None


def test_fallback_success_keeps_protocol_error_but_clears_overall_failure(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    db.record_protocol_failure("invalid_subscription")
    db.record_refresh_failure("all_sources_failed", 20)

    db.record_refresh_success(
        "digest",
        3,
        "yaml",
        {},
        21,
        source="fallback",
    )
    state = db.runtime_state()

    assert state["last_success_source"] == "fallback"
    assert state["consecutive_failures"] == 0
    assert state["last_error"] is None
    assert state["protocol_last_error_category"] == "invalid_subscription"


def test_settings_store_ignores_corrupt_values_instead_of_crashing(tmp_path: Path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings(key, value) VALUES (?, ?)",
            ("refresh_interval_minutes", "abc"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings(key, value) VALUES (?, ?)",
            ("health_refresh_online_ratio", "not-a-number"),
        )

    store = SettingsStore(db)
    settings = store.get()
    assert settings.refresh_interval_minutes == 60
    assert settings.health_refresh_online_ratio == 0.5

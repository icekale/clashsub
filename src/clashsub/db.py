import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admins (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  csrf_hash TEXT NOT NULL,
  csrf_token TEXT,
  admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
  expires_at REAL NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS shares (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  allow_raw INTEGER NOT NULL CHECK (allow_raw IN (0, 1)),
  allow_clash INTEGER NOT NULL CHECK (allow_clash IN (0, 1)),
  token_version INTEGER,
  token_nonce BLOB,
  token_ciphertext BLOB,
  base_url TEXT,
  revoked_at REAL,
  last_access_at REAL,
  access_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS encrypted_secrets (
  name TEXT PRIMARY KEY,
  version INTEGER NOT NULL,
  nonce BLOB NOT NULL,
  ciphertext BLOB NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS node_health (
  name TEXT PRIMARY KEY,
  ok INTEGER NOT NULL,
  latency_ms REAL,
  checked_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  current_digest TEXT,
  node_count INTEGER NOT NULL DEFAULT 0,
  content_format TEXT,
  safe_headers_json TEXT NOT NULL DEFAULT '{}',
  last_attempt_at REAL,
  last_success_at REAL,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  last_success_source TEXT,
  protocol_last_login_at REAL,
  protocol_last_subscribe_at REAL,
  protocol_subscription_expires_at REAL,
  protocol_last_error_category TEXT
);
INSERT OR IGNORE INTO runtime_state(id) VALUES (1);
"""


RUNTIME_STATE_MIGRATIONS = (
    ("last_success_source", "ALTER TABLE runtime_state ADD COLUMN last_success_source TEXT"),
    ("protocol_last_login_at", "ALTER TABLE runtime_state ADD COLUMN protocol_last_login_at REAL"),
    (
        "protocol_last_subscribe_at",
        "ALTER TABLE runtime_state ADD COLUMN protocol_last_subscribe_at REAL",
    ),
    (
        "protocol_subscription_expires_at",
        "ALTER TABLE runtime_state ADD COLUMN protocol_subscription_expires_at REAL",
    ),
    (
        "protocol_last_error_category",
        "ALTER TABLE runtime_state ADD COLUMN protocol_last_error_category TEXT",
    ),
)

SHARE_MIGRATIONS = (
    ("token_version", "ALTER TABLE shares ADD COLUMN token_version INTEGER"),
    ("token_nonce", "ALTER TABLE shares ADD COLUMN token_nonce BLOB"),
    ("token_ciphertext", "ALTER TABLE shares ADD COLUMN token_ciphertext BLOB"),
    ("base_url", "ALTER TABLE shares ADD COLUMN base_url TEXT"),
)

SESSION_MIGRATIONS = (
    ("csrf_token", "ALTER TABLE sessions ADD COLUMN csrf_token TEXT"),
)


class Database:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            # 迁移必须串行化：读列名 + ALTER 在 BEGIN IMMEDIATE 里原子执行，
            # 并发初始化（例如 migrate CLI 与应用同时启动）的后者会阻塞后重读列名，
            # 而不是在同一列上重复 ALTER 而崩溃。
            conn.execute("BEGIN IMMEDIATE")
            try:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(runtime_state)")}
                for name, statement in RUNTIME_STATE_MIGRATIONS:
                    if name not in columns:
                        conn.execute(statement)
                        columns.add(name)
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(shares)")}
                for name, statement in SHARE_MIGRATIONS:
                    if name not in columns:
                        conn.execute(statement)
                        columns.add(name)
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
                for name, statement in SESSION_MIGRATIONS:
                    if name not in columns:
                        conn.execute(statement)
                        columns.add(name)
            finally:
                conn.execute("COMMIT")

    @contextmanager
    def transaction(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def runtime_state(self):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM runtime_state WHERE id = 1").fetchone()

    def record_refresh_success(
        self,
        digest: str,
        node_count: int,
        content_format: str,
        safe_headers: dict[str, str],
        attempted_at: float,
        source: str,
    ):
        with self.transaction() as conn:
            conn.execute(
                """UPDATE runtime_state
                   SET current_digest=?, node_count=?, content_format=?, safe_headers_json=?,
                       last_attempt_at=?, last_success_at=?, last_success_source=?,
                       consecutive_failures=0, last_error=NULL
                   WHERE id=1""",
                (
                    digest,
                    node_count,
                    content_format,
                    json.dumps(safe_headers, sort_keys=True),
                    attempted_at,
                    time.time(),
                    source,
                ),
            )

    def record_protocol_success(
        self,
        login_at: float,
        subscribe_at: float,
        expires_at: float | None,
    ):
        with self.transaction() as conn:
            conn.execute(
                """UPDATE runtime_state
                   SET protocol_last_login_at=?, protocol_last_subscribe_at=?,
                       protocol_subscription_expires_at=?, protocol_last_error_category=NULL
                   WHERE id=1""",
                (login_at, subscribe_at, expires_at),
            )

    def record_protocol_failure(self, category: str):
        with self.transaction() as conn:
            conn.execute(
                "UPDATE runtime_state SET protocol_last_error_category=? WHERE id=1",
                (category,),
            )

    def record_refresh_failure(self, error: str, attempted_at: float):
        with self.transaction() as conn:
            conn.execute(
                """UPDATE runtime_state
                   SET last_attempt_at=?, consecutive_failures=consecutive_failures+1, last_error=?
                   WHERE id=1""",
                (attempted_at, error),
            )

    def insert_share(
        self,
        share_id: str,
        label: str,
        token_hash: str,
        created_at: float,
        expires_at: float,
        allow_raw: bool,
        allow_clash: bool,
        token_version: int | None = None,
        token_nonce: bytes | None = None,
        token_ciphertext: bytes | None = None,
        base_url: str | None = None,
    ):
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO shares
                   (id, label, token_hash, created_at, expires_at, allow_raw, allow_clash,
                    token_version, token_nonce, token_ciphertext, base_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    share_id,
                    label,
                    token_hash,
                    created_at,
                    expires_at,
                    1 if allow_raw else 0,
                    1 if allow_clash else 0,
                    token_version,
                    token_nonce,
                    token_ciphertext,
                    base_url,
                ),
            )

    def list_shares(self):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM shares ORDER BY created_at DESC").fetchall()

    def get_share(self, share_id: str):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone()

    def find_share_by_hash(self, token_hash: str):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM shares WHERE token_hash = ?", (token_hash,)).fetchone()

    def update_share_expiry(self, share_id: str, expires_at: float):
        with self.transaction() as conn:
            conn.execute("UPDATE shares SET expires_at=? WHERE id=?", (expires_at, share_id))

    def revoke_share(self, share_id: str, revoked_at: float):
        with self.transaction() as conn:
            conn.execute("UPDATE shares SET revoked_at=? WHERE id=?", (revoked_at, share_id))

    def rotate_share(
        self,
        share_id: str,
        token_hash: str,
        token_version: int | None = None,
        token_nonce: bytes | None = None,
        token_ciphertext: bytes | None = None,
        base_url: str | None = None,
    ):
        with self.transaction() as conn:
            conn.execute(
                """UPDATE shares SET token_hash=?, token_version=?, token_nonce=?,
                   token_ciphertext=?, base_url=? WHERE id=?""",
                (token_hash, token_version, token_nonce, token_ciphertext, base_url, share_id),
            )

    def delete_share(self, share_id: str):
        with self.transaction() as conn:
            conn.execute("DELETE FROM shares WHERE id=?", (share_id,))

    def record_share_access(self, share_id: str, now: float):
        with self.transaction() as conn:
            conn.execute(
                "UPDATE shares SET last_access_at=?, access_count=access_count+1 WHERE id=?",
                (now, share_id),
            )

    def update_share_recovery(
        self,
        share_id: str,
        token_version: int,
        token_nonce: bytes,
        token_ciphertext: bytes,
        base_url: str,
    ):
        with self.transaction() as conn:
            conn.execute(
                """UPDATE shares SET token_version=?, token_nonce=?, token_ciphertext=?, base_url=?
                   WHERE id=?""",
                (token_version, token_nonce, token_ciphertext, base_url, share_id),
            )

    def get_encrypted_secret(self, name: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT name, version, nonce, ciphertext, updated_at FROM encrypted_secrets WHERE name=?",
                (name,),
            ).fetchone()

    def put_encrypted_secrets(self, records: dict[str, tuple[int, bytes, bytes]], updated_at: float):
        with self.transaction() as conn:
            for name, (version, nonce, ciphertext) in records.items():
                conn.execute(
                    """INSERT INTO encrypted_secrets(name, version, nonce, ciphertext, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(name) DO UPDATE SET version=excluded.version,
                         nonce=excluded.nonce, ciphertext=excluded.ciphertext,
                         updated_at=excluded.updated_at""",
                    (name, version, nonce, ciphertext, updated_at),
                )

    def replace_node_health(self, records: list[tuple[str, int, float | None, float]]):
        with self.transaction() as conn:
            conn.execute("DELETE FROM node_health")
            conn.executemany(
                "INSERT INTO node_health(name, ok, latency_ms, checked_at) VALUES (?, ?, ?, ?)",
                records,
            )

    def list_node_health(self):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM node_health ORDER BY name").fetchall()

    def bootstrap_admin(self, username: str, password_hash: str, updated_at: float):
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO admins(id, username, password_hash, updated_at) VALUES (1, ?, ?, ?)",
                (username, password_hash, updated_at),
            )

    def get_admin(self):
        with self.connect() as conn:
            return conn.execute("SELECT id, username, password_hash, updated_at FROM admins WHERE id=1").fetchone()

    def get_admin_by_username(self, username: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT id, username, password_hash, updated_at FROM admins WHERE username=?",
                (username,),
            ).fetchone()

    def insert_session(
        self,
        token_hash: str,
        csrf_hash: str,
        csrf_token: str,
        expires_at: float,
        created_at: float,
    ):
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions(token_hash, csrf_hash, csrf_token, admin_id, expires_at, created_at) VALUES (?, ?, ?, 1, ?, ?)",
                (token_hash, csrf_hash, csrf_token, expires_at, created_at),
            )

    def get_session(self, token_hash: str):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM sessions WHERE token_hash=?", (token_hash,)).fetchone()

    def set_session_csrf_token(self, token_hash: str, csrf_hash: str, csrf_token: str):
        with self.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET csrf_hash=?, csrf_token=? WHERE token_hash=?",
                (csrf_hash, csrf_token, token_hash),
            )

    def delete_session(self, token_hash: str):
        with self.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def delete_expired_sessions(self, now: float):
        with self.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))

    def delete_all_sessions(self):
        with self.transaction() as conn:
            conn.execute("DELETE FROM sessions")

    def update_admin_and_delete_sessions(self, username: str, password_hash: str, updated_at: float):
        with self.transaction() as conn:
            conn.execute(
                "UPDATE admins SET username=?, password_hash=?, updated_at=? WHERE id=1",
                (username, password_hash, updated_at),
            )
            conn.execute("DELETE FROM sessions")

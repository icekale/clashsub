from __future__ import annotations

import argparse
import time
from pathlib import Path
from urllib.parse import urlsplit

from .config import Settings
from .db import Database
from .secret_store import SecretStore
from .shares import token_hash


def _parse_link(value: str) -> tuple[str, str, str]:
    try:
        parsed = urlsplit(value.strip())
    except ValueError as exc:
        raise ValueError("invalid link") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid link")
    parts = parsed.path.split("/")
    if len(parts) != 3 or parts[0] or parts[1] not in {"raw", "clash"} or not parts[2]:
        raise ValueError("invalid link")
    return f"{parsed.scheme}://{parsed.netloc}", parts[1], parts[2]


def import_link(secret_store: SecretStore, db: Database, value: str) -> str:
    base_url, route, token = _parse_link(value)
    row = db.find_share_by_hash(token_hash(token))
    if row is None:
        raise ValueError("share not found")
    if row["revoked_at"] is not None or row["expires_at"] <= time.time():
        raise ValueError("share is inactive")
    if route == "raw" and not row["allow_raw"] or route == "clash" and not row["allow_clash"]:
        raise ValueError("route permission denied")
    if row["token_ciphertext"] and row["token_nonce"] and row["base_url"]:
        return "already_recoverable"
    version, nonce, ciphertext = secret_store.seal(f"share:{row['id']}", token)
    db.update_share_recovery(row["id"], version, nonce, ciphertext, base_url)
    return "imported"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import existing share links without echoing them")
    parser.add_argument("--links-file", type=Path, required=True)
    args = parser.parse_args()
    config = Settings.from_env()
    db = Database(config.data_dir / "state.db")
    db.initialize()
    store = SecretStore(db, config.encryption_key_file)
    if not store.available:
        print("migration failed: encrypted secret store unavailable")
        return 2
    imported = already = failed = 0
    try:
        lines = args.links_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        print("migration failed: input unavailable")
        return 2
    for line in lines:
        if not line.strip():
            continue
        try:
            result = import_link(store, db, line)
            if result == "imported":
                imported += 1
            else:
                already += 1
        except (ValueError, OSError):
            failed += 1
    print(f"migration complete: imported={imported} already_recoverable={already} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

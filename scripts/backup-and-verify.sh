#!/usr/bin/env bash
set -euo pipefail

# Daily backup and read-only health verification for ClashSub.
# Run on the Unraid host from the deployment directory:
#   bash scripts/backup-and-verify.sh [BASE_URL]

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$ROOT/backups"
mkdir -p "$BACKUP_DIR"

# SQLite .backup produces a consistent snapshot even while the app is running.
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$ROOT/data/state.db" ".backup '$BACKUP_DIR/state.db.$STAMP'" || cp "$ROOT/data/state.db" "$BACKUP_DIR/state.db.$STAMP"
else
  cp "$ROOT/data/state.db" "$BACKUP_DIR/state.db.$STAMP"
fi
cp "$ROOT/compose.yaml" "$BACKUP_DIR/compose.yaml.$STAMP"

# Keep the most recent 14 snapshots and the compose copy.
ls -1t "$BACKUP_DIR"/state.db.* 2>/dev/null | tail -n +15 | xargs -r rm -f --
ls -1t "$BACKUP_DIR"/compose.yaml.* 2>/dev/null | tail -n +15 | xargs -r rm -f --

LOG="$BACKUP_DIR/verify.log"
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  bash "$ROOT/scripts/verify.sh" "${1:-http://127.0.0.1:18083}"
} >> "$LOG" 2>&1
tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"

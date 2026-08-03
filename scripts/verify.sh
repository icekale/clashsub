#!/usr/bin/env bash
set -euo pipefail

# Read-only health verification for a deployed ClashSub instance.
# Run from the deployment directory (where compose.yaml lives):
#   bash scripts/verify.sh [BASE_URL]

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${1:-http://127.0.0.1:18083}"
fail=0

check() {
  local name="$1" ok="$2" detail="${3:-}"
  if [ "$ok" = "1" ]; then
    echo "PASS  $name${detail:+  ($detail)}"
  else
    echo "FAIL  $name${detail:+  ($detail)}"
    fail=1
  fi
}

statuses="$(docker compose -f "$ROOT/compose.yaml" ps --format '{{.Name}} {{.Status}}' 2>&1 || true)"
if printf '%s' "$statuses" | grep -q 'healthy' \
  && printf '%s' "$statuses" | grep -q 'clashsub'; then
  check "compose containers" 1 "$(printf '%s' "$statuses" | tr '\n' ';')"
else
  check "compose containers" 0 "$(printf '%s' "$statuses" | tr '\n' ';')"
fi

if docker compose -f "$ROOT/compose.yaml" exec -T clashsub python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:25500/version', timeout=5)" >/dev/null 2>&1; then
  check "converter sidecar" 1 "127.0.0.1:25500"
else
  check "converter sidecar" 0 "127.0.0.1:25500"
fi

health_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$BASE/healthz" || true)"
check "healthz" "$([ "$health_code" = "200" ] && echo 1 || echo 0)" "$health_code"

app_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$BASE/app/" || true)"
check "webui" "$([ "$app_code" = "200" ] && echo 1 || echo 0)" "$app_code"

jar="$(mktemp)"
trap 'rm -f "$jar"' EXIT
username_file="$ROOT/secrets/admin_username"
password_file="$ROOT/secrets/admin_password"
if [ -f "$username_file" ] && [ -f "$password_file" ]; then
  username="$(cat "$username_file")"
  password="$(printf '%s' "$(cat "$password_file")" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  login_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 -c "$jar" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$username\",\"password\":\"$password\"}" \
    "$BASE/api/auth/login" || true)"
  check "admin login" "$([ "$login_code" = "200" ] && echo 1 || echo 0)" "$login_code"
  if [ "$login_code" = "200" ]; then
    overview="$(curl -sS -b "$jar" --max-time 30 "$BASE/api/admin/overview" || true)"
    node_count="$(printf '%s' "$overview" | sed -n 's/.*"node_count":\([0-9][0-9]*\).*/\1/p')"
    failures="$(printf '%s' "$overview" | sed -n 's/.*"consecutive_failures":\([0-9][0-9]*\).*/\1/p')"
    source_name="$(printf '%s' "$overview" | sed -n 's/.*"last_success_source":"\([^"]*\)".*/\1/p')"
    if [ "${node_count:-0}" -gt 0 ] && [ "${failures:-1}" = "0" ]; then
      check "subscription cache" 1 "nodes=$node_count failures=$failures source=$source_name"
    else
      check "subscription cache" 0 "nodes=$node_count failures=$failures source=$source_name"
    fi
    logs="$(curl -sS -b "$jar" --max-time 30 "$BASE/api/admin/logs?limit=20" || true)"
    if printf '%s' "$logs" | grep -q '"lines":\[[^]]'; then
      check "recent logs" 1 "non-empty"
    else
      check "recent logs" 0 "empty"
    fi
  fi
else
  check "admin checks" 0 "secrets/admin_username or secrets/admin_password missing"
fi

raw_dir="$ROOT/data/cache/raw"
raw_count=0
if [ -d "$raw_dir" ]; then raw_count="$(find "$raw_dir" -maxdepth 1 -name '*.bin' | wc -l | tr -d ' ')"; fi
check "raw cache bounded" "$([ "${raw_count:-0}" -le 6 ] && echo 1 || echo 0)" "$raw_count digests"

exit "$fail"

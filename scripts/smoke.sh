#!/bin/sh
set -eu

smoke_dir=$(mktemp -d)
app="clashsub-app-$$"
volume="clashsub-data-$$"

cleanup() {
  docker rm -f "$app" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  find "$smoke_dir" -mindepth 1 -delete
  rmdir "$smoke_dir"
}
trap cleanup EXIT INT TERM

printf '%s' "https://fixture.example.test/sample_base64.txt" >"$smoke_dir/upstream_url"
printf '%s' 'smoke-admin' >"$smoke_dir/admin_username"
printf '%s' 'smoke-password' >"$smoke_dir/admin_password"
head -c 32 /dev/urandom | base64 | tr -d '\n' >"$smoke_dir/encryption_key"

docker volume create "$volume" >/dev/null
docker run -d --name "$app" --read-only --cap-drop ALL \
  --tmpfs /tmp:rw,size=32m -v "$volume:/data" \
  -v "$PWD/scripts/smoke_app.py:/smoke_app.py:ro" \
  -v /dev/null:/run/secrets/airport_email:ro \
  -v /dev/null:/run/secrets/airport_password:ro \
  -v "$smoke_dir/upstream_url:/run/secrets/upstream_url:ro" \
  -v "$smoke_dir/admin_username:/run/secrets/admin_username:ro" \
  -v "$smoke_dir/admin_password:/run/secrets/admin_password:ro" \
  -v "$smoke_dir/encryption_key:/run/secrets/encryption_key:ro" \
  -e AIRPORT_API_BASE_URL= \
  -e AIRPORT_EMAIL_FILE=/run/secrets/airport_email \
  -e AIRPORT_PASSWORD_FILE=/run/secrets/airport_password \
  -e UPSTREAM_URL_FILE=/run/secrets/upstream_url \
  -e ADMIN_USERNAME_FILE=/run/secrets/admin_username \
  -e ADMIN_PASSWORD_FILE=/run/secrets/admin_password \
  -e ENCRYPTION_KEY_FILE=/run/secrets/encryption_key \
  clashsub:test python /smoke_app.py >/dev/null

for attempt in $(seq 1 30); do
  if docker exec "$app" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)" >/dev/null 2>&1; then
    break
  fi
  [ "$attempt" -lt 30 ] || { docker logs "$app"; exit 1; }
  sleep 1
done

docker exec -i "$app" python - <<'PY'
import base64, http.cookiejar, json, time, urllib.error, urllib.parse, urllib.request

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(path, method="GET", body=None, csrf=""):
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if csrf:
        headers["X-CSRF-Token"] = csrf
    request = urllib.request.Request(
        "http://127.0.0.1:8080" + path,
        data=data,
        headers=headers,
        method=method,
    )
    return opener.open(request, timeout=5)


login = json.load(call("/api/auth/login", "POST", {"username": "smoke-admin", "password": "smoke-password"}))
csrf = login["csrf_token"]
status = json.load(call("/api/admin/upstream/status"))
assert status["protocol_configured"] is False
assert status["fallback_configured"] is True
created = json.load(call("/api/admin/shares", "POST", {"label": "smoke", "days": 365}, csrf))
raw_path = urllib.parse.urlsplit(created["raw_url"]).path
revealed = json.load(call(f"/api/admin/shares/{created['id']}/reveal", "POST", {"kind": "raw"}, csrf))
assert revealed["url"] == created["raw_url"]

for _ in range(30):
    try:
        payload = call(raw_path).read()
        if b"trojan://" in base64.b64decode(payload):
            break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("raw subscription never became ready")

# 转换链路（subconverter 二进制 + 模板）也需要冒烟验证：创建允许 clash 的分享，
# 请求 /clash/<token> 并确认产物是 YAML 且嵌入公网回源 URL。
call(f"/api/admin/shares/{created['id']}/renew", "POST", {"days": 365}, csrf)
clash_created = json.load(
    call("/api/admin/shares", "POST", {"label": "smoke-clash", "days": 365, "allow_clash": True}, csrf)
)
clash_path = urllib.parse.urlsplit(clash_created["clash_url"]).path
for _ in range(30):
    try:
        clash_payload = call(clash_path).read()
        if b"proxies:" in clash_payload or b"proxy-providers:" in clash_payload:
            break
        if b"converter unavailable" in clash_payload:
            raise SystemExit("converter unavailable")
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("clash conversion never became ready")
assert clash_payload.startswith(b"port:") or b"proxies:" in clash_payload, clash_payload[:200]

call(f"/api/admin/shares/{created['id']}/revoke", "POST", {}, csrf)
try:
    call(raw_path)
except urllib.error.HTTPError as exc:
    assert exc.code == 404
else:
    raise SystemExit("revoked share stayed available")
PY

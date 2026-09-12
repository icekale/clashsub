#!/usr/bin/env python3
r"""对真实 SubConverter-Extended 容器跑一遍端到端验证（需要 Docker）。

单测用 MockTransport 覆盖应用侧，这个脚本覆盖真正的集成缝隙：容器内转换器能否经
host.docker.internal 反取本应用的 /raw、各输出格式与扩展参数是否被真实上游接受、
诊断/统计端点能否读到真实数据。

准备（镜像里自带 pref.example.toml，按项目同样的方式打补丁后挂载）：

    mkdir -p /tmp/e2e/base /tmp/e2e/data
    docker create --name sc194-tmp aethersailor/subconverter-extended:v1.9.4
    docker cp sc194-tmp:/base/. /tmp/e2e/base/ && docker rm sc194-tmp
    python3 - <<'EOF'   # 去掉容器内不可写的 [template] 段，路径指向挂载目录
    import re, pathlib
    base = pathlib.Path("/tmp/e2e/base")
    text = (base / "pref.example.toml").read_text()
    text = re.sub(r"\[template\][\s\S]*$", "", text)
    text = re.sub(r"(?m)^base_path\s*=.*", "base_path = /base", text)
    text = re.sub(r"(?m)^data_path\s*=.*", "data_path = /data", text)
    (base / "pref.toml").write_text(text)
    EOF
    docker run -d --name sc194g -p 25507:25500 -v /tmp/e2e/base:/base -v /tmp/e2e/data:/data \\
        aethersailor/subconverter-extended:v1.9.4 /usr/local/bin/start-subconverter

运行（在仓库根目录，CONVERTER_BASE_URL 指向该容器）：

    ./.venv/bin/python scripts/e2e-converter.py
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import pathlib
import shutil
import ssl
import subprocess
import sys
import threading
import time

import httpx

REPO = pathlib.Path(__file__).resolve().parents[1]
ROOT = pathlib.Path(os.environ.get("E2E_DIR", "/tmp/clashsub-e2e"))
APP_PORT, UP_PORT = 8099, 8443
CONVERTER = os.environ.get("CONVERTER_BASE_URL", "http://127.0.0.1:25507")
APP = f"http://127.0.0.1:{APP_PORT}"

# 应用只接受 https 源，因此由本地自签 https 存根充当机场订阅。
CLASH = """\
port: 7890
proxies:
  - {name: "香港 01", type: ss, server: hk1.example.com, port: 443, cipher: aes-128-gcm, password: pass1, udp: true}
  - {name: "日本 02", type: trojan, server: jp2.example.com, port: 443, password: pass2, sni: jp2.example.com}
  - {name: "美国 03", type: ss, server: us3.example.com, port: 8388, cipher: chacha20-ietf-poly1305, password: pass3, udp: true}
proxy-groups:
  - {name: PROXY, type: select, proxies: ["香港 01", "日本 02", "美国 03"]}
rules:
  - MATCH,PROXY
"""

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'ok  ' if condition else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


class UpstreamHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/sub.txt"):
            body = CLASH.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/yaml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def ensure_cert() -> tuple[pathlib.Path, pathlib.Path]:
    cert, key = ROOT / "cert.pem", ROOT / "key.pem"
    if not (cert.exists() and key.exists()):
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key), "-out", str(cert),
             "-days", "5", "-nodes", "-subj", "/CN=127.0.0.1",
             "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
            check=True, capture_output=True,
        )
    return cert, key


def start_upstream() -> http.server.ThreadingHTTPServer:
    cert, key = ensure_cert()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", UP_PORT), UpstreamHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def write_secrets() -> dict[str, str]:
    secrets_dir = ROOT / "secrets"
    if secrets_dir.exists():
        shutil.rmtree(secrets_dir)
    secrets_dir.mkdir(parents=True)
    values = {
        "admin_username": "e2e-admin",
        "admin_password": "e2e-password-1234",
        "upstream_url": f"https://127.0.0.1:{UP_PORT}/sub.txt",
        "encryption_key": base64.b64encode(os.urandom(32)).decode(),
    }
    for name, value in values.items():
        (secrets_dir / name).write_text(value)
    return {name: str(secrets_dir / name) for name in values}


def start_app(files: dict[str, str]) -> subprocess.Popen:
    data_dir = ROOT / "appdata"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    frontend = ROOT / "frontenddist"
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / "index.html").write_text("<!doctype html><title>e2e</title>")
    env = os.environ | {
        "DATA_DIR": str(data_dir),
        "FRONTEND_DIR": str(frontend),
        "ADMIN_USERNAME_FILE": files["admin_username"],
        "ADMIN_PASSWORD_FILE": files["admin_password"],
        "UPSTREAM_URL_FILE": files["upstream_url"],
        "ENCRYPTION_KEY_FILE": files["encryption_key"],
        "CONVERTER_BASE_URL": CONVERTER,
        # 自签测试源 + 回环地址：信任本地证书并放开下载目标限制（生产不需要）。
        "DOWNLOAD_ALLOWED_CIDRS": "127.0.0.0/8",
        "SSL_CERT_FILE": str(ROOT / "cert.pem"),
        # 转换器在容器里，需要经 host.docker.internal 回宿主取源文件。
        "CONVERTER_SOURCE_BASE_URL": f"http://host.docker.internal:{APP_PORT}",
    }
    log = (ROOT / "app.log").open("wb")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "clashsub.app:production_app", "--factory",
         "--host", "0.0.0.0", "--port", str(APP_PORT), "--no-access-log"],
        cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT,
    )


def wait_ready(proc: subprocess.Popen, seconds: int = 40) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(f"{APP}/healthz", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    upstream = start_upstream()
    files = write_secrets()
    proc = start_app(files)
    try:
        if not wait_ready(proc):
            print((ROOT / "app.log").read_text()[-3000:])
            check("应用启动", False, "健康检查超时")
            return 1
        check("应用启动", True, APP)

        client = httpx.Client(base_url=APP, timeout=90, follow_redirects=False)
        login = client.post("/api/auth/login", json={"username": "e2e-admin", "password": "e2e-password-1234"})
        check("管理员登录", login.status_code == 200, f"HTTP {login.status_code}")
        headers = {"x-csrf-token": login.json()["csrf_token"]}

        settings = client.get("/api/admin/settings").json()
        settings.update({"lan_base_url": APP, "converter_enabled": True, "refresh_interval_minutes": 60})
        updated = client.put("/api/admin/settings", json=settings, headers=headers)
        check("打开回环转换开关", updated.status_code == 200, f"HTTP {updated.status_code} {updated.text[:200]}")

        refresh = client.post("/api/admin/upstream/refresh", headers=headers)
        refreshed = refresh.json() if refresh.status_code == 200 else {}
        check("抓取上游订阅", refreshed.get("updated") is True, f"HTTP {refresh.status_code} {refresh.text[:300]}")

        before = client.get("/api/admin/converter/diagnostics").json()
        check("诊断端点可用", before.get("available") is True, json.dumps(before, ensure_ascii=False)[:200])
        check("读到上游版本 1.9.4", before.get("version") == "1.9.4", str(before.get("version")))
        check("读到上游提交 2a0fde4", before.get("commit") == "2a0fde4", str(before.get("commit")))
        stats = before.get("statistics") or {}
        check("统计摘要含当日窗口", isinstance(stats.get("day"), dict), json.dumps(stats, ensure_ascii=False)[:260])

        created = client.post("/api/admin/shares",
                              json={"label": "e2e", "days": 7, "allow_raw": True, "allow_clash": True},
                              headers=headers)
        check("创建分享链接", created.status_code == 201, f"HTTP {created.status_code} {created.text[:200]}")
        share_id = created.json()["id"]
        reveal = client.post(f"/api/admin/shares/{share_id}/reveal", json={"kind": "raw"}, headers=headers)
        token = reveal.json()["url"].rstrip("/").split("/")[-1]
        check("取回分享 token", bool(token))

        markers = {"/quanx/": "quantumult", "/surfboard/": "surfboard"}
        outputs: dict[str, str] = {}
        for prefix in ("/raw/", "/clash/", "/clash-ha/", "/surge/", "/loon/", "/quanx/", "/surfboard/", "/singbox/"):
            response = client.get(f"{prefix}{token}")
            outputs[prefix] = response.text
            marker = markers.get(prefix)
            ok = response.status_code == 200 and len(response.text) > 200
            if marker:
                ok = ok and marker in response.text.lower()
            check(f"GET {prefix}<token>", ok, f"HTTP {response.status_code}, {len(response.text)} 字节")

        check("sing-box 输出是 JSON 配置", "outbounds" in outputs["/singbox/"])
        check("quanx 输出含 server 段", "server" in outputs["/quanx/"].lower())
        check("surfboard 输出含 proxy 段", "[proxy]" in outputs["/surfboard/"].lower())

        plain = client.get(f"/clash/{token}").text
        udp = client.get(f"/clash/{token}?udp=true&tfo=true")
        check("扩展参数返回 200", udp.status_code == 200, f"HTTP {udp.status_code}")
        check("tfo=true 生效", "tfo" in udp.text.lower())
        check("参数分组独立缓存", udp.text != plain)

        # 上游统计是周期性快照（有延迟），轮询直到计数增长。
        earlier = (stats.get("lifetime") or {}).get("rule_conversions")
        later = earlier
        deadline = time.time() + 45
        while time.time() < deadline:
            document = client.get("/api/admin/converter/diagnostics").json()
            later = ((document.get("statistics") or {}).get("lifetime") or {}).get("rule_conversions")
            if isinstance(later, int) and isinstance(earlier, int) and later > earlier:
                break
            time.sleep(3)
        check("统计随转换请求增长", isinstance(later, int) and isinstance(earlier, int) and later > earlier,
              f"rule_conversions {earlier} -> {later}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        upstream.shutdown()

    print()
    if failures:
        print(f"FAILED: {len(failures)} 项 -> {failures}")
        return 1
    print("端到端全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

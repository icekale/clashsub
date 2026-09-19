import base64

import httpx
import yaml
from fastapi.testclient import TestClient
from pydantic import SecretStr

from clashsub.app import create_app
from clashsub.config import Settings
from clashsub.settings import RuntimeSettings
from clashsub.tailscale_proxy import HOSTNAME, PROXY_NAME, SECRET_NAME, inject_tailscale


def test_inject_appends_proxy_and_group_member():
    document = {
        "proxies": [{"name": "hk", "type": "ss"}],
        "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": ["hk", "DIRECT"]}],
    }
    inject_tailscale(document, "tskey-auth-test", "100.64.0.1")
    assert document["proxies"][-1] == {
        "name": PROXY_NAME,
        "type": "tailscale",
        "hostname": HOSTNAME,
        "auth-key": "tskey-auth-test",
        "exit-node": "100.64.0.1",
        "exit-node-allow-lan-access": True,
        "udp": True,
    }
    assert PROXY_NAME in document["proxy-groups"][0]["proxies"]


def test_inject_skips_blank_key():
    document = {"proxies": [{"name": "hk", "type": "ss"}]}
    inject_tailscale(document, "  ", "100.64.0.1")
    assert document["proxies"] == [{"name": "hk", "type": "ss"}]


def test_inject_skips_without_exit_node():
    """没有 exit node 时不能注入，否则选中该节点会完全断网。"""
    document = {"proxies": [{"name": "hk", "type": "ss"}]}
    inject_tailscale(document, "tskey-auth-test", "  ")
    assert document["proxies"] == [{"name": "hk", "type": "ss"}]


def test_inject_replaces_existing_same_name():
    document = {
        "proxies": [
            {"name": PROXY_NAME, "type": "tailscale", "auth-key": "old"},
            {"name": "hk", "type": "ss"},
        ],
        "proxy-groups": [{"name": "PROXY", "proxies": [PROXY_NAME, "hk"]}],
    }
    inject_tailscale(document, "new-key", "100.64.0.2")
    names = [item["name"] for item in document["proxies"]]
    assert names.count(PROXY_NAME) == 1
    assert document["proxies"][-1]["auth-key"] == "new-key"
    assert document["proxy-groups"][0]["proxies"].count(PROXY_NAME) == 1


def _app_settings(tmp_path):
    key = tmp_path / "key"
    key.write_text(base64.b64encode(b"k" * 32).decode(), encoding="ascii")
    return Settings(
        data_dir=tmp_path / "data",
        frontend_dir=tmp_path / "frontend",
        upstream_url=SecretStr("https://provider.invalid/sub?token=hidden"),
        initial_username=SecretStr("user"),
        initial_password=SecretStr("pass"),
        encryption_key_file=key,
        converter_source_base_url="http://clashsub:8080",
    )


CLASH_BODY = (
    "proxies:\n"
    "  - name: hk\n"
    "    type: ss\n"
    "proxy-groups:\n"
    "  - name: PROXY\n"
    "    type: select\n"
    "    proxies: [hk, DIRECT]\n"
)


def _converter_handler(request: httpx.Request) -> httpx.Response:
    target = request.url.params.get("target")
    if target == "clash":
        return httpx.Response(200, text=CLASH_BODY)
    if target == "loon":
        return httpx.Response(200, text="Working = trojan, example.test, 443, password\n")
    return httpx.Response(200, text="ok\n")


def _seed_share(client):
    services = client.app.state.services
    created = services.shares.create("friend", allow_clash=True)
    token = created.raw_url.rsplit("/", 1)[1]
    digest = services.cache.publish_raw(
        CLASH_BODY.encode(),
        {"subscription-userinfo": "upload=1"},
    )
    services.db.record_refresh_success(digest, 1, "yaml", {"subscription-userinfo": "upload=1"}, 1, source="fallback")
    return token


def test_tailscale_credentials_and_clash_inject(tmp_path):
    app = create_app(
        _app_settings(tmp_path),
        start_scheduler=False,
        transport=httpx.MockTransport(_converter_handler),
    )
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        services = client.app.state.services
        services.runtime_settings.update(
            RuntimeSettings(
                lan_base_url="http://testserver",
                converter_enabled=True,
                tailscale_enabled=True,
                tailscale_exit_node="100.64.0.1",
            )
        )
        login = client.post("/api/auth/login", json={"username": "user", "password": "pass"})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert client.get("/api/admin/tailscale/credentials").json() == {"configured": False}
        saved = client.put(
            "/api/admin/tailscale/credentials",
            headers=headers,
            json={"secret": "tskey-auth-test"},
        )
        assert saved.status_code == 200
        token = _seed_share(client)
        clash = client.get(f"/clash/{token}")
        loon = client.get(f"/loon/{token}")
        ha = client.get(f"/clash-ha/{token}")
    assert clash.status_code == 200
    document = yaml.safe_load(clash.content)
    assert document["proxies"][-1]["type"] == "tailscale"
    assert document["proxies"][-1]["auth-key"] == "tskey-auth-test"
    assert document["proxies"][-1]["exit-node"] == "100.64.0.1"
    assert PROXY_NAME in document["proxy-groups"][0]["proxies"]
    assert "tailscale" not in loon.text.lower()
    ha_doc = yaml.safe_load(ha.content)
    assert ha_doc["proxies"][-1]["type"] == "tailscale"
    assert PROXY_NAME in ha_doc["proxy-groups"][0]["proxies"]


def test_clash_skips_tailscale_when_disabled(tmp_path):
    app = create_app(
        _app_settings(tmp_path),
        start_scheduler=False,
        transport=httpx.MockTransport(_converter_handler),
    )
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        services = client.app.state.services
        services.runtime_settings.update(
            RuntimeSettings(lan_base_url="http://testserver", converter_enabled=True)
        )
        services.credential_store.put(SECRET_NAME, "tskey-auth-test")
        token = _seed_share(client)
        clash = client.get(f"/clash/{token}")
    document = yaml.safe_load(clash.content)
    assert all(item.get("type") != "tailscale" for item in document.get("proxies", []))


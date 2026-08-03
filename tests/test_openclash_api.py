import base64
import time
from contextlib import contextmanager

import httpx
import yaml
from fastapi.testclient import TestClient
from pydantic import SecretStr

from clashsub.app import create_app
from clashsub.app import build_services
from clashsub.config import Settings
from clashsub.settings import RuntimeSettings


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
    )


def _login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "user", "password": "pass"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


@contextmanager
def _client(app):
    with TestClient(app, client=("127.0.0.1", 50000)) as test_client:
        test_client.app.state.services.runtime_settings.update(
            RuntimeSettings(lan_base_url="http://testserver")
        )
        yield test_client


def test_openclash_credentials_roundtrip(tmp_path):
    app = create_app(_app_settings(tmp_path), start_scheduler=False)
    with _client(app) as client:
        csrf = _login(client)
        headers = {"X-CSRF-Token": csrf}
        assert client.get("/api/admin/openclash/credentials").json() == {"configured": False}
        response = client.put(
            "/api/admin/openclash/credentials",
            headers=headers,
            json={"secret": "s3cret"},
        )
        assert response.status_code == 200
        assert client.get("/api/admin/openclash/credentials").json() == {"configured": True}


def test_settings_roundtrip_includes_integration_fields(tmp_path):
    app = create_app(_app_settings(tmp_path), start_scheduler=False)
    with _client(app) as client:
        csrf = _login(client)
        headers = {"X-CSRF-Token": csrf}
        current = client.get("/api/admin/settings").json()
        payload = {
            **current,
            "openclash_enabled": True,
            "openclash_api_url": "http://192.168.1.1:9090",
            "openclash_provider": "Provider_988009",
            "health_enabled": True,
            "health_interval_minutes": 15,
            "health_timeout_seconds": 8,
        }
        response = client.put("/api/admin/settings", headers=headers, json=payload)
        assert response.status_code == 200
        saved = client.get("/api/admin/settings").json()
        assert saved["openclash_enabled"] is True
        assert saved["openclash_provider"] == "Provider_988009"
        assert saved["health_interval_minutes"] == 15
        assert saved["health_timeout_seconds"] == 8

        invalid = client.put(
            "/api/admin/settings",
            headers=headers,
            json={**payload, "openclash_api_url": ""},
        )
        assert invalid.status_code == 400


def test_openclash_test_endpoint(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"meta": True, "version": "alpha-smart"})

    app = create_app(
        _app_settings(tmp_path),
        start_scheduler=False,
        transport=httpx.MockTransport(handler),
    )
    with _client(app) as client:
        csrf = _login(client)
        headers = {"X-CSRF-Token": csrf}
        response = client.post(
            "/api/admin/openclash/test",
            headers=headers,
            json={"api_url": "http://192.168.1.1:9090", "secret": "secret"},
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True, "version": "alpha-smart", "meta": True}


def test_openclash_test_reports_unauthorized(tmp_path):
    app = create_app(
        _app_settings(tmp_path),
        start_scheduler=False,
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )
    with _client(app) as client:
        csrf = _login(client)
        headers = {"X-CSRF-Token": csrf}
        response = client.post(
            "/api/admin/openclash/test",
            headers=headers,
            json={"api_url": "http://192.168.1.1:9090", "secret": "wrong"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "unauthorized"


def _seed_ha_subscription(client):
    services = client.app.state.services
    created = services.shares.create("ha-test", allow_clash=True)
    token = created.raw_url.rsplit("/", 1)[1]
    payload = (
        "proxies:\n"
        '  - name: "OK Node"\n'
        "    type: ss\n"
        "    server: node.example.test\n"
        "    port: 443\n"
        '  - name: "Dead Node"\n'
        "    type: ss\n"
        "    server: dead.example.test\n"
        "    port: 443\n"
        "proxy-groups:\n"
        '  - name: "Proxy"\n'
        "    type: url-test\n"
        '    proxies: ["OK Node", "Dead Node"]\n'
    ).encode()
    digest = services.cache.publish_raw(payload, {"subscription-userinfo": "upload=99"})
    services.db.record_refresh_success(
        digest,
        2,
        "yaml",
        {"subscription-userinfo": "upload=99"},
        time.time() - 3600,
        source="fallback",
    )
    return token


def test_clash_ha_filters_recently_failed_nodes(tmp_path):
    app = create_app(
        _app_settings(tmp_path),
        start_scheduler=False,
        transport=httpx.MockTransport(lambda request: httpx.Response(502)),
    )
    with _client(app) as client:
        services = client.app.state.services
        token = _seed_ha_subscription(client)
        services.db.replace_node_health(
            [
                ("OK Node", 1, 12.0, time.time()),
                ("Dead Node", 0, None, time.time()),
            ]
        )
        response = client.get(f"/clash-ha/{token}")
        assert response.status_code == 200
        assert response.headers["subscription-userinfo"] == "upload=99"
        document = yaml.safe_load(response.content)
        names = [proxy["name"] for proxy in document["proxies"]]
        assert names == ["OK Node"]


def test_clash_ha_fails_open_without_health_data(tmp_path):
    app = create_app(
        _app_settings(tmp_path),
        start_scheduler=False,
        transport=httpx.MockTransport(lambda request: httpx.Response(502)),
    )
    with _client(app) as client:
        token = _seed_ha_subscription(client)
        response = client.get(f"/clash-ha/{token}")
        assert response.status_code == 200
        document = yaml.safe_load(response.content)
        assert len(document["proxies"]) == 2


def test_clash_ha_fails_open_with_stale_health(tmp_path):
    app = create_app(
        _app_settings(tmp_path),
        start_scheduler=False,
        transport=httpx.MockTransport(lambda request: httpx.Response(502)),
    )
    with _client(app) as client:
        services = client.app.state.services
        token = _seed_ha_subscription(client)
        services.db.replace_node_health(
            [
                ("OK Node", 1, 12.0, time.time() - 7200),
                ("Dead Node", 0, None, time.time() - 7200),
            ]
        )
        response = client.get(f"/clash-ha/{token}")
        document = yaml.safe_load(response.content)
        assert len(document["proxies"]) == 2


def test_health_overview_and_manual_check(tmp_path):
    app = create_app(
        _app_settings(tmp_path),
        start_scheduler=False,
        transport=httpx.MockTransport(lambda request: httpx.Response(502)),
    )
    with _client(app) as client:
        csrf = _login(client)
        headers = {"X-CSRF-Token": csrf}
        overview = client.get("/api/admin/health").json()
        assert overview["total"] == 0
        assert overview["enabled"] is False
        client.put(
            "/api/admin/settings",
            headers=headers,
            json={
                **client.get("/api/admin/settings").json(),
                "health_enabled": True,
                "health_interval_minutes": 5,
                "health_timeout_seconds": 2,
            },
        )
        _seed_ha_subscription(client)
        result = client.post("/api/admin/health/check", headers=headers)
        assert result.status_code == 200
        body = result.json()
        assert body["total"] == 2
        assert body["online"] == 0  # fake domains do not resolve
        overview = client.get("/api/admin/health").json()
        assert overview["total"] == 2
        assert overview["checked_at"] is not None


def test_refresher_hook_is_wired_to_integration(tmp_path):
    services = build_services(_app_settings(tmp_path))
    assert services.refresher.on_refreshed is not None
    assert services.integration is not None
    assert services.health_scheduler is None  # only created inside the app lifespan

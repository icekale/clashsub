import base64
import time
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient

from clashsub.app import create_app
from clashsub.db import Database
from clashsub.settings import RuntimeSettings


async def public_test_resolver(hostname: str, port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


def successful_upstream_transport():
    payload = base64.b64encode(b"trojan://pass@node.example:443#one\n")

    def handler(request):
        if request.url.path.endswith("/passport/auth/login"):
            return httpx.Response(200, json={"data": {"auth_data": "native-auth"}})
        if request.url.path.endswith("/user/getSubscribe"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "subscribe_url": "https://sub.invalid/path?token=hidden",
                        "expired_at": 1_900_000_000,
                    }
                },
            )
        assert request.headers["user-agent"] == "BBGen2UA"
        return httpx.Response(200, content=payload)

    return httpx.MockTransport(handler)


def failing_upstream_transport():
    return httpx.MockTransport(lambda request: httpx.Response(502, content=b"unavailable"))


def login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "initial-user", "password": "initial-password"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_spa_history_fallback_requires_lan_access(app_settings):
    app_settings.frontend_dir.mkdir(parents=True)
    app_settings.frontend_dir.joinpath("index.html").write_text(
        "<!doctype html><title>ClashSub test UI</title>",
        encoding="utf-8",
    )

    with TestClient(
        create_app(app_settings, start_scheduler=False),
        client=("192.168.1.20", 50000),
    ) as client:
        assert "ClashSub test UI" in client.get("/app/login").text
        assert "ClashSub test UI" in client.get("/app/shares").text

    with TestClient(
        create_app(app_settings, start_scheduler=False),
        client=("8.8.8.8", 50000),
    ) as client:
        assert client.get("/app/login").status_code == 404


def test_spa_assets_are_served_with_immutable_cache_and_reject_traversal(app_settings):
    app_settings.frontend_dir.mkdir(parents=True)
    assets = app_settings.frontend_dir / "assets"
    assets.mkdir(parents=True)
    (assets / "app-abc123.js").write_text("console.log('app')", encoding="utf-8")
    app_settings.frontend_dir.joinpath("index.html").write_text(
        "<!doctype html><title>ClashSub test UI</title>",
        encoding="utf-8",
    )
    # A decoy outside the frontend dir proves the route stays contained.
    outside = app_settings.data_dir.parent / "secret.txt"
    outside.write_text("top-secret", encoding="utf-8")

    with TestClient(
        create_app(app_settings, start_scheduler=False),
        client=("192.168.1.20", 50000),
    ) as client:
        index = client.get("/app/")
        assert index.status_code == 200
        assert index.headers["cache-control"] == "no-cache"
        asset = client.get("/app/assets/app-abc123.js")
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        # Encoded-dot / encoded-slash traversals must never escape the assets dir.
        # (Plain .. segments are normalized away by clients/servers before routing.)
        for traversal in (
            "/app/assets/..%2f..%2fsecret.txt",
            "/app/assets/..%2f..%2f..%2f..%2fsecret.txt",
            "/app/assets/%2e%2e/%2e%2e/secret.txt",
            "/app/assets/%2e%2e%2f%2e%2e%2fsecret.txt",
            "/app/assets/..%5c..%5csecret.txt",
        ):
            response = client.get(traversal)
            assert response.status_code == 404, f"traversal not blocked: {traversal}"
            assert "top-secret" not in response.text


def test_share_format_paths_require_lan_access_when_public_mode_is_off(app_settings):
    with TestClient(
        create_app(app_settings, start_scheduler=False),
        client=("8.8.8.8", 50000),
    ) as client:
        for prefix in ("raw", "clash", "surge", "loon", "smart"):
            assert client.get(f"/{prefix}/known-token").status_code == 404


def test_startup_removes_expired_sessions(app_settings):
    app_settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(app_settings.data_dir / "state.db")
    db.initialize()
    db.bootstrap_admin("initial-user", "hash", 0)
    db.insert_session("expired-hash", "csrf-hash", "csrf-token", expires_at=1, created_at=0)

    with TestClient(
        create_app(app_settings, start_scheduler=False),
        client=("127.0.0.1", 50000),
    ) as client:
        assert client.app.state.services.db.get_session("expired-hash") is None


def test_deleting_share_removes_converted_templates(app_settings):
    with TestClient(
        create_app(app_settings, start_scheduler=False),
        client=("127.0.0.1", 50000),
    ) as client:
        csrf = login(client)
        created = client.post(
            "/api/admin/shares",
            headers={"X-CSRF-Token": csrf},
            json={"label": "temporary", "allow_clash": True},
        ).json()
        client.app.state.services.cache.write_converter_template(created["id"], "template", "clash")

        assert (
            client.delete(
                f"/api/admin/shares/{created['id']}",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 204
        )

        converted = client.app.state.services.cache.root / "converted"
        assert not list(converted.glob(f"{created['id']}*"))


def test_admin_share_actions_are_logged(app_settings):
    with TestClient(
        create_app(app_settings, start_scheduler=False),
        client=("127.0.0.1", 50000),
    ) as client:
        csrf = login(client)
        created = client.post(
            "/api/admin/shares",
            headers={"X-CSRF-Token": csrf},
            json={"label": "audit-friend", "days": 30},
        ).json()
        assert (
            client.post(
                f"/api/admin/shares/{created['id']}/revoke",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 204
        )

    lines = (app_settings.data_dir / "logs" / "events.log").read_text(encoding="utf-8").splitlines()
    assert any("share created" in line and "audit-friend" in line for line in lines)
    assert any("share revoked" in line for line in lines)


def test_converter_failure_is_logged(app_settings):
    with TestClient(
        create_app(app_settings, transport=httpx.MockTransport(lambda request: httpx.Response(502))),
        client=("127.0.0.1", 50000),
    ) as client:
        services = client.app.state.services
        services.runtime_settings.update(
            RuntimeSettings(lan_base_url="http://testserver", converter_enabled=True)
        )
        created = services.shares.create("friend", allow_clash=True)
        token = created.raw_url.rsplit("/", 1)[1]

        assert client.get(f"/surge/{token}").status_code == 503

    lines = (app_settings.data_dir / "logs" / "events.log").read_text(encoding="utf-8").splitlines()
    assert any("converter unavailable" in line for line in lines)


def test_share_request_triggers_on_demand_refresh_when_stale(app_settings):
    payload = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(200, content=payload)

    config = replace(
        app_settings,
        airport_api_base_url=None,
        airport_email=None,
        airport_password=None,
    )
    with TestClient(
        create_app(
            config,
            transport=httpx.MockTransport(handler),
            resolver=public_test_resolver,
            start_scheduler=False,
        ),
        client=("127.0.0.1", 50000),
    ) as client:
        services = client.app.state.services
        created = services.shares.create("local")
        token = created.raw_url.rsplit("/", 1)[1]
        digest = services.cache.publish_raw(b"old-cache", {})
        services.db.record_refresh_success(digest, 1, "yaml", {}, 1, source="fallback")

        first = client.get(f"/raw/{token}")
        assert first.status_code == 200 and first.content == b"old-cache"
        assert calls["count"] == 0

        with services.db.connect() as conn:
            conn.execute("UPDATE runtime_state SET last_success_at=?", (time.time() - 7200,))
        second = client.get(f"/raw/{token}")

        assert second.status_code == 200
        assert second.content == payload
        assert calls["count"] == 1


def test_share_request_serves_stale_cache_when_refresh_fails(app_settings):
    def handler(request):
        return httpx.Response(502, content=b"unavailable")

    config = replace(
        app_settings,
        airport_api_base_url=None,
        airport_email=None,
        airport_password=None,
    )
    with TestClient(
        create_app(
            config,
            transport=httpx.MockTransport(handler),
            resolver=public_test_resolver,
            start_scheduler=False,
        ),
        client=("127.0.0.1", 50000),
    ) as client:
        services = client.app.state.services
        created = services.shares.create("local")
        token = created.raw_url.rsplit("/", 1)[1]
        digest = services.cache.publish_raw(b"old-cache", {})
        services.db.record_refresh_success(digest, 1, "yaml", {}, 1, source="fallback")
        with services.db.connect() as conn:
            conn.execute("UPDATE runtime_state SET last_success_at=?", (time.time() - 7200,))

        response = client.get(f"/raw/{token}")

        assert response.status_code == 200
        assert response.content == b"old-cache"


def test_refresh_exception_serves_stale_cache(app_settings):
    payload = base64.b64encode(b"trojan://pass@node.example:443#one\n")

    def handler(request):
        return httpx.Response(200, content=payload)

    config = replace(
        app_settings,
        airport_api_base_url=None,
        airport_email=None,
        airport_password=None,
    )
    with TestClient(
        create_app(
            config,
            transport=httpx.MockTransport(handler),
            resolver=public_test_resolver,
            start_scheduler=False,
        ),
        client=("127.0.0.1", 50000),
    ) as client:
        services = client.app.state.services
        created = services.shares.create("local")
        token = created.raw_url.rsplit("/", 1)[1]
        digest = services.cache.publish_raw(b"old-cache", {})
        services.db.record_refresh_success(digest, 1, "yaml", {}, 1, source="fallback")

        async def boom(*args, **kwargs):
            raise RuntimeError("boom")

        services.refresher.refresh_if_stale = boom

        response = client.get(f"/raw/{token}")

        assert response.status_code == 200
        assert response.content == b"old-cache"


def test_share_and_raw_cache_survive_application_restart(app_settings):
    first = create_app(
        app_settings,
        transport=successful_upstream_transport(),
        resolver=public_test_resolver,
    )
    with TestClient(first, client=("127.0.0.1", 50000)) as client:
        csrf = login(client)
        created = client.post(
            "/api/admin/shares",
            headers={"X-CSRF-Token": csrf},
            json={"label": "persist", "days": 365},
        ).json()
        token = created["raw_url"].rsplit("/", 1)[1]
        refreshed = client.post(
            "/api/admin/upstream/refresh",
            headers={"X-CSRF-Token": csrf},
        ).json()
        assert refreshed["source"] == "protocol"

    second = create_app(app_settings, transport=failing_upstream_transport())
    with TestClient(second, client=("127.0.0.1", 50000)) as client:
        response = client.get(f"/raw/{token}")
        assert response.status_code == 200
        assert b"trojan://" in base64.b64decode(response.content)
        csrf = login(client)
        overview = client.get("/api/admin/overview").json()
        assert overview["last_success_source"] == "protocol"
        assert overview["protocol_subscription_expires_at"] == 1_900_000_000

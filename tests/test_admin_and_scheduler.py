import asyncio
import base64
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from clashsub.app import create_app
from clashsub.scheduler import RefreshScheduler


def login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "initial-user", "password": "initial-password"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _successful_credential_transport():
    payload = base64.b64encode(b"trojan://pass@node.example:443#one\n")

    def handler(request):
        if request.url.path.endswith("/passport/auth/login"):
            return httpx.Response(200, json={"data": {"auth_data": "native-auth"}})
        if request.url.path.endswith("/user/getSubscribe"):
            return httpx.Response(
                200,
                json={"data": {"subscribe_url": "https://sub.example.test/path", "expired_at": 1_900_000_000}},
            )
        return httpx.Response(200, content=payload)

    return httpx.MockTransport(handler)


async def _public_test_resolver(hostname, port):
    return ("93.184.216.34",)


def test_admin_requires_session_and_manages_one_time_share(client):
    assert client.get("/api/admin/overview").status_code == 401
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    created = client.post(
        "/api/admin/shares",
        headers=headers,
        json={"label": "friend", "days": 365, "allow_clash": False},
    ).json()
    assert "/raw/" in created["raw_url"]
    listed = client.get("/api/admin/shares").json()
    assert "raw_url" not in listed[0] and "token" not in str(listed)
    assert client.post(f"/api/admin/shares/{created['id']}/revoke", headers=headers).status_code == 204


def test_public_mode_requires_acknowledgement_and_logs_out(client):
    csrf = login(client)
    payload = {
        "refresh_interval_minutes": 10,
        "access_mode": "public",
        "lan_base_url": "http://nas:18080",
        "public_base_url": "https://sub.example.com",
        "converter_enabled": True,
    }
    assert client.put("/api/admin/settings", headers={"X-CSRF-Token": csrf}, json=payload).status_code == 400
    response = client.put(
        "/api/admin/settings",
        headers={"X-CSRF-Token": csrf},
        json={**payload, "public_acknowledged": True},
    )
    assert response.status_code == 200 and response.json()["reauthenticate"] is True
    assert client.get("/api/admin/overview").status_code == 401


def test_upstream_status_is_redacted(client):
    login(client)

    response = client.get("/api/admin/upstream/status")

    assert response.status_code == 200
    assert response.json() == {
        "protocol_configured": True,
        "api_base_url": "https://panel.example.test/api/v1",
        "email_configured": True,
        "password_configured": True,
        "fallback_configured": True,
    }
    serialized = json.dumps(response.json())
    assert "member@example.test" not in serialized
    assert "airport-pass" not in serialized


def test_overview_includes_redacted_protocol_runtime_status(client):
    login(client)

    overview = client.get("/api/admin/overview").json()

    assert overview["last_success_source"] is None
    assert overview["protocol_last_login_at"] is None
    assert overview["protocol_last_subscribe_at"] is None
    assert overview["protocol_subscription_expires_at"] is None
    assert overview["protocol_last_error_category"] is None


def test_upstream_test_requires_csrf_and_never_returns_url(app_settings):
    payload = base64.b64encode(b"trojan://pass@node.example:443#one\n")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/passport/auth/login"):
            return httpx.Response(200, json={"data": {"auth_data": "native-auth"}})
        if request.url.path.endswith("/user/getSubscribe"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "subscribe_url": "https://sub.example.test/path?token=hidden",
                        "expired_at": 1_900_000_000,
                    }
                },
            )
        return httpx.Response(200, content=payload)

    with TestClient(
        create_app(app_settings, transport=httpx.MockTransport(handler)),
        client=("127.0.0.1", 50000),
    ) as protocol_client:
        assert protocol_client.post("/api/admin/upstream/test").status_code == 401
        csrf = login(protocol_client)
        assert protocol_client.post("/api/admin/upstream/test").status_code == 403

        response = protocol_client.post(
            "/api/admin/upstream/test",
            headers={"X-CSRF-Token": csrf},
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "error_category": None,
        "expires_at": 1_900_000_000,
    }
    assert "subscribe_url" not in response.text
    assert "native-auth" not in response.text
    assert "hidden" not in response.text


def test_upstream_credentials_validate_before_save_and_never_echo_password(app_settings, tmp_path):
    key = tmp_path / "encryption-key"
    key.write_bytes(base64.b64encode(b"k" * 32))
    settings = replace(app_settings, encryption_key_file=key)
    with TestClient(
            create_app(
                settings,
                transport=_successful_credential_transport(),
                resolver=_public_test_resolver,
            ),
        client=("127.0.0.1", 50000),
    ) as protocol_client:
        csrf = login(protocol_client)
        before = protocol_client.get("/api/admin/upstream/credentials")
        assert before.status_code == 200
        assert before.json() == {
            "username": "member@example.test",
            "password_configured": True,
            "management_available": True,
        }

        response = protocol_client.put(
            "/api/admin/upstream/credentials",
            headers={"X-CSRF-Token": csrf},
            json={"username": "updated@example.test", "password": "updated-pass"},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "node_count": 1, "error_category": None}
        assert "updated-pass" not in response.text
        assert protocol_client.get("/api/admin/upstream/credentials").json()["username"] == "updated@example.test"


@pytest.mark.asyncio
async def test_scheduler_refreshes_immediately_and_stops_cleanly():
    calls = []

    class FakeRefresher:
        async def refresh(self):
            calls.append("refresh")

    scheduler = RefreshScheduler(FakeRefresher(), delay_seconds=lambda: 3600)
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0)
    await scheduler.stop()
    await task
    assert calls == ["refresh"]

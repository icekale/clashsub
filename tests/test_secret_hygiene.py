import base64

import httpx
from fastapi.testclient import TestClient

from clashsub.app import create_app


async def public_test_resolver(hostname: str, port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


def test_runtime_files_do_not_contain_plain_secrets(client, app_settings):
    services = client.app.state.services
    created = services.shares.create("secret-scan", allow_clash=True)
    token = created.raw_url.rsplit("/", 1)[1]
    client.get(f"/raw/{token}")

    forbidden = [
        app_settings.upstream_url.get_secret_value().encode(),
        app_settings.initial_password.get_secret_value().encode(),
        app_settings.airport_email.get_secret_value().encode(),
        app_settings.airport_password.get_secret_value().encode(),
        token.encode(),
    ]
    for path in app_settings.data_dir.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            for value in forbidden:
                assert value not in content, f"plaintext secret found in {path.name}"


def test_protocol_refresh_does_not_persist_credentials_or_upstream_tokens(app_settings):
    auth_data = "fake-native-auth-secret"
    subscription_url = "https://sub.invalid/path?token=fake-upstream-token"
    payload = base64.b64encode(b"trojan://pass@node.example:443#one\n")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/passport/auth/login"):
            return httpx.Response(200, json={"data": {"auth_data": auth_data}})
        if request.url.path.endswith("/user/getSubscribe"):
            return httpx.Response(
                200,
                json={"data": {"subscribe_url": subscription_url}},
            )
        return httpx.Response(200, content=payload)

    with TestClient(
        create_app(
            app_settings,
            transport=httpx.MockTransport(handler),
            resolver=public_test_resolver,
        ),
        client=("127.0.0.1", 50000),
    ) as protocol_client:
        login_response = protocol_client.post(
            "/api/auth/login",
            json={"username": "initial-user", "password": "initial-password"},
        )
        csrf = login_response.json()["csrf_token"]
        refresh = protocol_client.post(
            "/api/admin/upstream/refresh",
            headers={"X-CSRF-Token": csrf},
        )
        assert refresh.status_code == 200
        assert refresh.json()["source"] == "protocol"

    forbidden = [
        app_settings.airport_email.get_secret_value().encode(),
        app_settings.airport_password.get_secret_value().encode(),
        auth_data.encode(),
        subscription_url.encode(),
    ]
    for path in app_settings.data_dir.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            for value in forbidden:
                assert value not in content, f"plaintext protocol secret found in {path.name}"

from dataclasses import replace

import httpx
from fastapi.testclient import TestClient

from clashsub.app import create_app
from clashsub.settings import RuntimeSettings


def login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "initial-user", "password": "initial-password"},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _converted(body: str):
    """转换服务返回一份足够过校验的最小正文，并记录每次请求。"""
    calls = []

    def handler(request):
        calls.append(request.url)
        if request.url.path == "/internal/raw":
            return httpx.Response(200, text="ss://raw-from-internal\n")
        return httpx.Response(200, text=body)

    return calls, httpx.MockTransport(handler)


def _prepare(client, raw=b"raw-payload"):
    services = client.app.state.services
    services.runtime_settings.update(
        RuntimeSettings(lan_base_url="http://testserver", converter_enabled=True)
    )
    digest = services.cache.publish_raw(raw, {})
    services.db.record_refresh_success(digest, 1, "yaml", {}, 1, source="fallback")
    return services


def test_internal_raw_is_loopback_only_even_with_a_spoofed_forwarded_header(app_settings):
    with TestClient(
        create_app(app_settings, transport=httpx.MockTransport(lambda request: httpx.Response(200))),
        client=("127.0.0.1", 50000),
    ) as client:
        _prepare(client, raw=b"raw-for-converter")
        allowed = client.get("/internal/raw")

    with TestClient(
        create_app(app_settings, transport=httpx.MockTransport(lambda request: httpx.Response(200))),
        client=("203.0.113.7", 50000),
    ) as client:
        _prepare(client)
        refused = client.get("/internal/raw", headers={"X-Forwarded-For": "127.0.0.1"})

    assert allowed.status_code == 200
    assert allowed.content == b"raw-for-converter"
    assert refused.status_code == 404


def test_preview_converts_the_configured_upstream_without_creating_a_share(app_settings):
    calls, transport = _converted("proxy-providers:\n  p:\n    type: http\n")
    with TestClient(
        create_app(
            replace(app_settings, converter_source_base_url="http://clashsub:8080"),
            transport=transport,
        ),
        client=("127.0.0.1", 50000),
    ) as client:
        csrf = login(client)
        services = _prepare(client)
        response = client.post(
            "/api/admin/converter/preview",
            headers={"X-CSRF-Token": csrf},
            json={"kind": "clash", "params": {"udp": "true", "config": "/etc/passwd"}},
        )

        assert services.shares.list() == []

    assert response.status_code == 200
    assert response.json()["body"].startswith("proxy-providers")
    # 上游源走内部通道：没有 token，也不把白名单之外的参数转给转换服务。
    assert calls[0].path == "/sub"
    assert calls[0].params["url"] == "http://clashsub:8080/internal/raw"
    assert calls[0].params["target"] == "clash"
    assert calls[0].params["udp"] == "true"
    assert "config" not in calls[0].params


def test_preview_surge_uses_the_upstream_dialect_without_a_share(app_settings):
    calls, transport = _converted("[General]\n[Proxy]\nNode = surge, example.test, 443\n[Rule]\n")
    with TestClient(
        create_app(
            replace(app_settings, converter_source_base_url="http://clashsub:8080"),
            transport=transport,
        ),
        client=("127.0.0.1", 50000),
    ) as client:
        csrf = login(client)
        _prepare(client)
        response = client.post(
            "/api/admin/converter/preview",
            headers={"X-CSRF-Token": csrf},
            json={"kind": "surge", "params": {"emoji": "false"}},
        )

    assert response.status_code == 200
    assert calls[0].params["ver"] == "4"
    assert calls[0].params["emoji"] == "false"
    # 预览没有客户端可用的地址，MANAGED-CONFIG 只能指内部通道；正式使用走保存出来的分享链接。
    assert '#!MANAGED-CONFIG http://clashsub:8080/internal/raw?emoji=false interval=' in response.json()["body"]


def test_preview_reports_unavailable_converter_and_requires_admin(app_settings):
    def failing(request):
        return httpx.Response(500, text="boom")

    with TestClient(
        create_app(
            replace(app_settings, converter_source_base_url="http://clashsub:8080"),
            transport=httpx.MockTransport(failing),
        ),
        client=("127.0.0.1", 50000),
    ) as client:
        csrf = login(client)
        _prepare(client)
        unavailable = client.post(
            "/api/admin/converter/preview",
            headers={"X-CSRF-Token": csrf},
            json={"kind": "clash"},
        )
        anonymous = client.post("/api/admin/converter/preview", json={"kind": "clash"})

    assert unavailable.status_code == 503
    assert anonymous.status_code == 403


def test_preview_rejects_a_format_the_converter_cannot_produce(app_settings):
    calls, transport = _converted("proxy-providers: {}\n")
    with TestClient(
        create_app(
            replace(app_settings, converter_source_base_url="http://clashsub:8080"),
            transport=transport,
        ),
        client=("127.0.0.1", 50000),
    ) as client:
        csrf = login(client)
        _prepare(client)
        response = client.post(
            "/api/admin/converter/preview",
            headers={"X-CSRF-Token": csrf},
            json={"kind": "stash"},
        )

    assert response.status_code == 422
    assert calls == []


def test_preview_needs_an_active_base_url(app_settings):
    calls, transport = _converted("proxy-providers: {}\n")
    with TestClient(create_app(app_settings, transport=transport), client=("127.0.0.1", 50000)) as client:
        csrf = login(client)
        services = _prepare(client)
        services.runtime_settings.update(RuntimeSettings(converter_enabled=True))
        response = client.post(
            "/api/admin/converter/preview",
            headers={"X-CSRF-Token": csrf},
            json={"kind": "clash"},
        )

    assert response.status_code == 400
    assert calls == []

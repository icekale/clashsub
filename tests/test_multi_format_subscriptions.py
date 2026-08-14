import asyncio
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from clashsub.app import create_app
from clashsub.cache_files import CacheFiles
from clashsub.converter import ConverterService
from clashsub.settings import RuntimeSettings


@pytest.mark.asyncio
async def test_converter_uses_target_and_keeps_format_caches_isolated(tmp_path):
    seen = []

    def handler(request):
        target = request.url.params["target"]
        seen.append(target)
        raw_url = request.url.params["url"]
        if target == "clash":
            return httpx.Response(200, text=f"proxy-providers:\n  p:\n    type: http\n    url: {raw_url}\n")
        if target == "surge" and request.url.params.get("ver") != "4":
            return httpx.Response(
                200,
                text="[General]\nloglevel = notify\n[Proxy]\nDIRECT = direct\n",
            )
        return httpx.Response(
            200,
            text=f"[General]\nloglevel = notify\n[Proxy]\nNode = {target}, example.test, 443\n# {raw_url}\n",
        )

    service = ConverterService(CacheFiles(tmp_path), "https://converter.example.test", httpx.MockTransport(handler))
    share_id = "00000000-0000-0000-0000-000000000001"
    raw_url = "https://sub.example.test/raw/token"
    await service.render(share_id, raw_url, "clash")
    surge = await service.render(share_id, raw_url, "surge")
    loon = await service.render(share_id, raw_url, "loon")

    assert seen == ["clash", "surge", "loon"]
    assert "[Proxy]" in surge and "[Proxy]" in loon
    assert (tmp_path / "converted" / f"{share_id}.yaml").exists()
    assert (tmp_path / "converted" / f"{share_id}-surge.conf").exists()
    assert (tmp_path / "converted" / f"{share_id}-loon.conf").exists()


def test_smart_route_uses_user_agent_and_returns_raw_for_shadowrocket_or_unknown(app_settings):
    raw_urls = []

    def handler(request):
        target = request.url.params["target"]
        raw_url = request.url.params["url"]
        raw_urls.append(raw_url)
        if target == "clash":
            return httpx.Response(200, text=f"proxy-providers:\n  p:\n    type: http\n    url: {raw_url}\n")
        return httpx.Response(
            200,
            text=f"[General]\nloglevel = notify\n[Proxy]\nNode = {target}, example.test, 443\n",
        )

    with TestClient(
        create_app(
            replace(app_settings, converter_source_base_url="http://clashsub:8080"),
            transport=httpx.MockTransport(handler),
        ),
        client=("127.0.0.1", 50000),
    ) as client:
        services = client.app.state.services
        services.runtime_settings.update(
            RuntimeSettings(lan_base_url="http://testserver", converter_enabled=True)
        )
        created = services.shares.create("friend", allow_clash=True)
        token = created.raw_url.rsplit("/", 1)[1]
        digest = services.cache.publish_raw(b"raw-clash-yaml", {})
        services.db.record_refresh_success(digest, 1, "yaml", {}, 1, source="fallback")

        surge = client.get(f"/smart/{token}", headers={"User-Agent": "Surge iOS"})
        loon = client.get(f"/smart/{token}", headers={"User-Agent": "Loon/3"})
        clash = client.get(f"/smart/{token}", headers={"User-Agent": "Mihomo"})
        stash = client.get(f"/smart/{token}", headers={"User-Agent": "Stash/2.4"})
        shadowrocket = client.get(f"/smart/{token}", headers={"User-Agent": "Shadowrocket/2.1"})
        unknown = client.get(f"/smart/{token}", headers={"User-Agent": "curl"})

    assert "[Proxy]" in surge.text
    assert "[Proxy]" in loon.text
    assert "proxy-providers" in clash.text
    assert "proxy-providers" in stash.text
    assert shadowrocket.content == b"raw-clash-yaml"
    assert f"http://testserver/raw/{token}" in clash.text
    assert "http://clashsub:8080/raw/" not in clash.text
    assert unknown.content == b"raw-clash-yaml"
    assert raw_urls == [f"http://clashsub:8080/raw/{token}"] * 3


def test_converted_routes_404_without_active_base_url(app_settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="proxy-providers: {}\n")

    with TestClient(
        create_app(app_settings, transport=httpx.MockTransport(handler)),
        client=("127.0.0.1", 50000),
    ) as client:
        services = client.app.state.services
        services.runtime_settings.update(
            RuntimeSettings(lan_base_url="http://testserver", converter_enabled=True)
        )
        created = services.shares.create("friend", allow_clash=True)
        token = created.raw_url.rsplit("/", 1)[1]
        digest = services.cache.publish_raw(b"raw", {})
        services.db.record_refresh_success(digest, 1, "yaml", {}, 1, source="fallback")
        services.runtime_settings.update(RuntimeSettings(lan_base_url="", converter_enabled=True))

        response = client.get(f"/clash/{token}")

    assert response.status_code == 404
    assert calls == []


def test_converted_responses_forward_subscription_headers(app_settings):
    safe_headers = {
        "subscription-userinfo": "upload=1; download=2; total=3; expire=1900000000",
        "profile-update-interval": "24",
    }

    def handler(request):
        target = request.url.params["target"]
        raw_url = request.url.params["url"]
        if target == "clash":
            return httpx.Response(200, text=f"proxy-providers:\n  p:\n    type: http\n    url: {raw_url}\n")
        return httpx.Response(
            200,
            text=f"[General]\nloglevel = notify\n[Proxy]\nNode = {target}, example.test, 443\n",
        )

    with TestClient(
        create_app(
            replace(app_settings, converter_source_base_url="http://clashsub:8080"),
            transport=httpx.MockTransport(handler),
        ),
        client=("127.0.0.1", 50000),
    ) as client:
        services = client.app.state.services
        services.runtime_settings.update(
            RuntimeSettings(lan_base_url="http://testserver", converter_enabled=True)
        )
        created = services.shares.create("friend", allow_clash=True)
        token = created.raw_url.rsplit("/", 1)[1]
        digest = services.cache.publish_raw(b"raw-clash-yaml", safe_headers)
        services.db.record_refresh_success(digest, 1, "yaml", safe_headers, 1, source="fallback")

        for path in ("clash", "surge", "loon"):
            response = client.get(f"/{path}/{token}")
            assert response.status_code == 200
            assert response.headers["subscription-userinfo"] == safe_headers["subscription-userinfo"]
            assert response.headers["profile-update-interval"] == safe_headers["profile-update-interval"]

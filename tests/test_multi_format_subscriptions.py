from dataclasses import replace
from pathlib import Path
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from clashsub.app import create_app
from clashsub.cache_files import CacheFiles
from clashsub.converter import (
    BOOLEAN_PARAMS,
    CONVERTER_FORMATS,
    TEMPLATES,
    ConverterService,
    client_params,
    params_key,
)
from clashsub.settings import RuntimeSettings


ROOT = Path(__file__).resolve().parents[1]


def _body(target: str, raw_url: str) -> str:
    """每种格式返回一份能过服务端校验的最小正文。"""
    if target == "clash":
        return f"proxy-providers:\n  p:\n    type: http\n    url: {raw_url}\n"
    if target == "singbox":
        return '{"outbounds": [{"type": "vless", "server": "example.test", "server_port": 443}]}'
    if target == "quanx":
        return (
            "[general]\nloglevel = notify\n[server_local]\n"
            'Node = example.test:443, chacha20-ietf-poly1305, "pw"\n'
        )
    return f"[General]\nloglevel = notify\n[Proxy]\nNode = {target}, example.test, 443\n# {raw_url}\n"


def _handler(request: httpx.Request, seen: list | None = None) -> httpx.Response:
    params = request.url.params
    if seen is not None:
        seen.append({"target": params["target"], "ver": params.get("ver"), "params": params})
    return httpx.Response(200, text=_body(params["target"], params["url"]))


@pytest.mark.asyncio
async def test_converter_uses_target_and_keeps_format_caches_isolated(tmp_path):
    seen = []
    service = ConverterService(
        CacheFiles(tmp_path),
        "https://converter.example.test",
        httpx.MockTransport(lambda request: _handler(request, seen)),
    )
    share_id = "00000000-0000-0000-0000-000000000001"
    raw_url = "https://sub.example.test/raw/token"
    rendered = {
        format: await service.render(share_id, raw_url, format) for format in CONVERTER_FORMATS
    }

    assert [entry["target"] for entry in seen] == list(CONVERTER_FORMATS)
    # Surge 需要 ver=4 的方言，其余格式不带 ver。
    ver_by_target = {entry["target"]: entry["ver"] for entry in seen}
    assert ver_by_target["surge"] == "4"
    assert all(ver_by_target[format] is None for format in CONVERTER_FORMATS if format != "surge")

    assert "[Proxy]" in rendered["surge"] and "[Proxy]" in rendered["loon"]
    assert "[server_local]" in rendered["quanx"] and "[Proxy]" in rendered["surfboard"]
    assert '"server"' in rendered["singbox"]

    assert (tmp_path / "converted" / f"{share_id}.yaml").exists()
    for format, suffix in (
        ("surge", "conf"),
        ("loon", "conf"),
        ("quanx", "conf"),
        ("surfboard", "conf"),
        ("singbox", "json"),
    ):
        assert (tmp_path / "converted" / f"{share_id}-{format}.{suffix}").exists()


@pytest.mark.asyncio
async def test_client_parameters_reach_upstream_and_split_the_cache(tmp_path):
    seen = []
    service = ConverterService(
        CacheFiles(tmp_path),
        "https://converter.example.test",
        httpx.MockTransport(lambda request: _handler(request, seen)),
    )
    share_id = "00000000-0000-0000-0000-000000000002"
    raw_url = "https://sub.example.test/raw/token"
    query = {
        "emoji": "false",
        "udp": "1",
        "ver": "3",
        # 白名单之外的一律丢弃：config 能读容器内文件，rename 会让上游编译客户端正则。
        "config": "/etc/passwd",
        "rename": ".*",
        "upload": "http://attacker.invalid/",
    }
    params = client_params(query)

    await service.render(share_id, raw_url, "clash")
    await service.render(share_id, raw_url, "clash", params=params)

    assert params == {"emoji": "false", "udp": "true", "ver": "3"}
    assert seen[0]["params"].get("emoji") is None
    forwarded = seen[1]["params"]
    assert forwarded["emoji"] == "false" and forwarded["udp"] == "true" and forwarded["ver"] == "3"
    assert not {"config", "rename", "upload"} & set(forwarded)

    # 带参数的渲染必须与默认渲染分开缓存，否则第一份模板会被复用。
    key = params_key(params)
    assert key and key != params_key(None)
    assert (tmp_path / "converted" / f"{share_id}.yaml").exists()
    assert (tmp_path / "converted" / f"{share_id}-clash-{key}.yaml").exists()


@pytest.mark.asyncio
async def test_template_alias_becomes_config_and_drops_raw_config(tmp_path):
    """template 是我们的别名；config 永远不从客户端直通。"""
    seen = []
    service = ConverterService(
        CacheFiles(tmp_path),
        "https://converter.example.test",
        httpx.MockTransport(lambda request: _handler(request, seen)),
    )
    share_id = "00000000-0000-0000-0000-000000000003"
    raw_url = "https://sub.example.test/raw/token"
    params = client_params({"template": "lite", "config": "/etc/passwd"})

    await service.render(share_id, raw_url, "clash", params=params)

    assert params == {"config": TEMPLATES["lite"]}
    assert seen[0]["params"]["config"] == TEMPLATES["lite"]
    assert "template" not in seen[0]["params"]
    assert client_params({"template": "../etc/passwd"}) == {}
    assert client_params({"template": "Custom_Clash.ini"}) == {}
    assert set(TEMPLATES) == {
        "standard",
        "standard-fallback",
        "lite",
        "lite-fallback",
        "gfw",
        "gfw-fallback",
        "full",
        "full-fallback",
    }


def test_frontend_parameter_picker_stays_inside_the_whitelist():
    """前端勾选面板抄一份参数表，这里守住两边不漂移。"""
    source = (ROOT / "frontend" / "src" / "shareView.js").read_text(encoding="utf-8")
    block = re.search(r"export const CONVERT_PARAMS = \[(.*?)\n\]", source, re.S)
    assert block, "shareView.js 里的 CONVERT_PARAMS 结构变了，同步更新这个测试"
    keys = set(re.findall(r"key: '([a-z_]+)'", block.group(1)))

    assert keys == set(BOOLEAN_PARAMS) | {"ver", "template"}
    templates = re.search(r"export const CONVERT_TEMPLATES = \[(.*?)\n\]", source, re.S)
    assert templates, "shareView.js 里的 CONVERT_TEMPLATES 结构变了，同步更新这个测试"
    assert set(re.findall(r"value: '([a-z-]+)'", templates.group(1))) == set(TEMPLATES)

    # 面板只挂在真的会读查询串的路由上：/raw 直出、/clash-ha 本地过滤都不看参数。
    kinds = set(re.search(r"export const CONVERTER_KINDS = \[(.*?)\]", source, re.S).group(1)
                 .replace("'", "").replace(" ", "").split(","))
    assert kinds == set(CONVERTER_FORMATS) | {"smart"}


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

    with TestClient(
        create_app(
            replace(app_settings, converter_source_base_url="http://clashsub:8080"),
            transport=httpx.MockTransport(_handler),
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

        for path in CONVERTER_FORMATS:
            response = client.get(f"/{path}/{token}")
            assert response.status_code == 200
            assert response.headers["subscription-userinfo"] == safe_headers["subscription-userinfo"]
            assert response.headers["profile-update-interval"] == "1"
            expected = "application/json" if path == "singbox" else None
            if expected:
                assert response.headers["content-type"].startswith(expected)


def test_smart_route_uses_user_agent_and_returns_raw_for_shadowrocket_or_unknown(app_settings):
    raw_urls = []

    def handler(request):
        raw_urls.append(request.url.params["url"])
        return _handler(request)

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
        quanx = client.get(f"/smart/{token}", headers={"User-Agent": "Quantumult X/1.0.8"})
        surfboard = client.get(f"/smart/{token}", headers={"User-Agent": "Surfboard/2.5"})
        singbox = client.get(f"/smart/{token}", headers={"User-Agent": "sing-box 1.10.0"})
        shadowrocket = client.get(f"/smart/{token}", headers={"User-Agent": "Shadowrocket/2.1"})
        unknown = client.get(f"/smart/{token}", headers={"User-Agent": "curl"})

    assert "[Proxy]" in surge.text
    assert "[Proxy]" in loon.text
    assert "proxy-providers" in clash.text
    # Stash 读 Clash 配置：同一个格式的第二个请求命中缓存，不再打上游。
    assert "proxy-providers" in stash.text
    assert "[server_local]" in quanx.text
    assert "[Proxy]" in surfboard.text
    assert '"server"' in singbox.text
    assert shadowrocket.content == b"raw-clash-yaml"
    assert f"http://testserver/raw/{token}" in clash.text
    assert "http://clashsub:8080/raw/" not in clash.text
    assert unknown.content == b"raw-clash-yaml"
    assert raw_urls == [f"http://clashsub:8080/raw/{token}"] * 6

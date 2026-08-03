import httpx
import pytest
from urllib.parse import quote

from clashsub.cache_files import CacheFiles
from clashsub.converter import RAW_URL_PLACEHOLDER, ConverterService


VALID = """port: 7890
proxy-providers:
  Provider_A:
    type: http
    url: https://sub.example.com/raw/plain-secret
"""


@pytest.mark.asyncio
async def test_conversion_uses_sub_endpoint_and_sanitizes_disk(tmp_path):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["url"] = request.url.params["url"]
        seen["expand"] = request.url.params["expand"]
        return httpx.Response(200, text=VALID)

    service = ConverterService(
        CacheFiles(tmp_path),
        "https://api.asailor.org",
        transport=httpx.MockTransport(handler),
    )
    share_id = "00000000-0000-0000-0000-000000000001"
    result = await service.render(share_id, "https://sub.example.com/raw/plain-secret")
    stored = (tmp_path / "converted" / f"{share_id}.yaml").read_text(encoding="utf-8")
    assert seen == {
        "path": "/sub",
        "url": "https://sub.example.com/raw/plain-secret",
        "expand": "true",
    }
    assert "plain-secret" in result
    assert "plain-secret" not in stored
    assert RAW_URL_PLACEHOLDER in stored


@pytest.mark.asyncio
async def test_invalid_response_falls_back_but_first_failure_is_unavailable(tmp_path):
    responses = iter([httpx.Response(200, text=VALID), httpx.Response(502, text="bad")])
    service = ConverterService(
        CacheFiles(tmp_path),
        "https://api.asailor.org",
        transport=httpx.MockTransport(lambda request: next(responses)),
        cache_ttl=0,
    )
    share_id = "00000000-0000-0000-0000-000000000001"
    first = await service.render(share_id, "https://sub.example.com/raw/plain-secret")
    second = await service.render(share_id, "https://sub.example.com/raw/plain-secret")
    assert second == first
    empty = ConverterService(
        CacheFiles(tmp_path / "empty"),
        "https://api.asailor.org",
        transport=httpx.MockTransport(lambda request: httpx.Response(502)),
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        await empty.render("00000000-0000-0000-0000-000000000002", "https://sub.example.com/raw/another-secret")


@pytest.mark.asyncio
async def test_clash_accepts_inline_proxies_from_local_converter(tmp_path):
    payload = """port: 7890
proxies:
  - name: Node
    type: ss
    server: example.test
    port: 443
proxy-groups:
  - name: Proxy
    type: select
    proxies: [Node]
"""
    service = ConverterService(
        CacheFiles(tmp_path),
        "http://subconverter:25500",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )

    assert await service.render(
        "00000000-0000-0000-0000-000000000006", "http://clashsub:8080/raw/token", "clash"
    ) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "[Proxy]\n",
        "[Proxy]\nNode = ss, example.test, 443\n",
        "[General]\nloglevel = notify\n[Proxy]\n# no proxies\n",
        "[General]\nloglevel = notify\n[Proxy]\nNode = invalid\n",
        "[General]\nloglevel = notify\n[Proxy]\nNode = ss,\n",
        "[General]\nloglevel = notify\n[Proxy]\nNode = invalid,anything\n",
    ],
)
async def test_surge_and_loon_reject_structurally_invalid_output(tmp_path, payload):
    service = ConverterService(
        CacheFiles(tmp_path),
        "https://converter.example.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )

    with pytest.raises(RuntimeError, match="unavailable"):
        await service.render("00000000-0000-0000-0000-000000000003", "https://sub.example/raw/token", "surge")


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["surge", "loon"])
async def test_surge_and_loon_accept_general_and_a_proxy_entry(tmp_path, format):
    payload = "[General]\nloglevel = notify\n[Proxy]\nNode = ss, example.test, 443\n"
    service = ConverterService(
        CacheFiles(tmp_path),
        "https://converter.example.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )

    rendered = await service.render(
        "00000000-0000-0000-0000-000000000004", "https://sub.example/raw/token", format
    )

    if format == "surge":
        assert rendered == "#!MANAGED-CONFIG https://sub.example/surge/token interval=3600\n" + payload
    else:
        assert rendered == payload


@pytest.mark.asyncio
async def test_surge_removes_managed_header_and_encoded_token_from_cache(tmp_path):
    raw_url = "http://clashsub:8080/raw/plain-secret"
    payload = (
        f"#!MANAGED-CONFIG http://127.0.0.1:25500/sub?target=surge&url={quote(raw_url, safe='')} interval=86400\n"
        "[General]\nloglevel = notify\n[Proxy]\nNode = ss, example.test, 443\n"
    )
    cache = CacheFiles(tmp_path)
    service = ConverterService(
        cache,
        "http://subconverter:25500",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )

    rendered = await service.render(
        "00000000-0000-0000-0000-000000000005", raw_url, "surge"
    )
    stored = cache.read_converter_template("00000000-0000-0000-0000-000000000005", "surge")

    assert "127.0.0.1:25500" not in rendered
    assert rendered.startswith("#!MANAGED-CONFIG http://clashsub:8080/surge/plain-secret interval=3600\n")
    assert "plain-secret" not in stored


@pytest.mark.asyncio
async def test_surge_managed_header_uses_public_url_and_cache_hits(tmp_path):
    payload = "[General]\nloglevel = notify\n[Proxy]\nNode = ss, example.test, 443\n"
    service = ConverterService(
        CacheFiles(tmp_path),
        "http://subconverter:25500",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )
    share_id = "00000000-0000-0000-0000-000000000014"

    first = await service.render(
        share_id,
        "http://clashsub:8080/raw/secret",
        "surge",
        public_raw_url="https://sub.example.com/raw/secret",
    )
    second = await service.render(
        share_id,
        "http://clashsub:8080/raw/secret",
        "surge",
        public_raw_url="http://192.168.1.10:18080/raw/secret",
    )

    assert first.startswith("#!MANAGED-CONFIG https://sub.example.com/surge/secret interval=3600\n")
    assert second.startswith("#!MANAGED-CONFIG http://192.168.1.10:18080/surge/secret interval=3600\n")
    assert "http://clashsub:8080" not in first


@pytest.mark.asyncio
async def test_surge_output_drops_geosite_rules_and_keeps_direct_fallback(tmp_path):
    payload = (
        "[General]\nloglevel = notify\n"
        "[Proxy]\nNode = ss, example.test, 443\n"
        "[Rule]\nGEOSITE,private,组1\nGEOIP,cn,组2,no-resolve\nFINAL,🐟 漏网之鱼\n"
    )
    service = ConverterService(
        CacheFiles(tmp_path),
        "http://subconverter:25500",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )

    rendered = await service.render(
        "00000000-0000-0000-0000-000000000007", "http://clashsub:8080/raw/token", "surge"
    )

    assert "GEOSITE," not in rendered
    assert "GEOIP,CN,DIRECT" in rendered


@pytest.mark.asyncio
async def test_surge_output_keeps_existing_cn_direct_rule(tmp_path):
    payload = (
        "[General]\nloglevel = notify\n"
        "[Proxy]\nNode = ss, example.test, 443\n"
        "[Rule]\nGEOSITE,cn,组1\nGEOIP,CN,DIRECT,no-resolve\nFINAL,组2\n"
    )
    service = ConverterService(
        CacheFiles(tmp_path),
        "http://subconverter:25500",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )

    rendered = await service.render(
        "00000000-0000-0000-0000-000000000008", "http://clashsub:8080/raw/token", "surge"
    )

    assert "GEOSITE," not in rendered
    assert rendered.count("GEOIP,CN,DIRECT") == 1


@pytest.mark.asyncio
async def test_surge_output_fills_empty_ws_path(tmp_path):
    payload = (
        "[General]\nloglevel = notify\n"
        "[Proxy]\n"
        "Node = vmess, example.test, 443, username=abc, ws=true, ws-path=, udp-relay=true\n"
        "[Rule]\nFINAL,DIRECT\n"
    )
    service = ConverterService(
        CacheFiles(tmp_path),
        "http://subconverter:25500",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )

    rendered = await service.render(
        "00000000-0000-0000-0000-000000000009", "http://clashsub:8080/raw/token", "surge"
    )

    assert "ws-path=/" in rendered
    assert "ws-path=," not in rendered


@pytest.mark.asyncio
async def test_surge_output_drops_non_country_geoip_rules(tmp_path):
    payload = (
        "[General]\nloglevel = notify\n"
        "[Proxy]\nNode = ss, example.test, 443\n"
        "[Rule]\n"
        "GEOIP,private,🎯 全球直连,no-resolve\n"
        "GEOIP,telegram,💬 即时通讯,no-resolve\n"
        "GEOIP,cn,🎯 全球直连,no-resolve\n"
        "GEOIP,CN,DIRECT\n"
        "FINAL,🐟 漏网之鱼\n"
    )
    service = ConverterService(
        CacheFiles(tmp_path),
        "http://subconverter:25500",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )

    rendered = await service.render(
        "00000000-0000-0000-0000-000000000010", "http://clashsub:8080/raw/token", "surge"
    )

    assert "GEOIP,private" not in rendered
    assert "GEOIP,telegram" not in rendered
    assert "GEOIP,cn" in rendered
    assert "GEOIP,CN,DIRECT" in rendered


@pytest.mark.asyncio
async def test_surge_injects_ws_params_and_skip_cert_from_source(tmp_path):
    raw_yaml = (
        "proxies:\n"
        '  - name: "🇭🇰 香港-实验线路 BGP"\n'
        "    type: vmess\n"
        "    server: node.example.test\n"
        "    port: 32521\n"
        "    network: ws\n"
        "    ws-opts:\n"
        "      Path: /secret-path\n"
        "      headers:\n"
        "        Host: spoof.example.test\n"
        '  - name: "🇭🇰 香港-广东专线 BGP 1"\n'
        "    type: trojan\n"
        "    server: node.example.test\n"
        "    port: 32443\n"
        "    sni: spoof.example.test\n"
        "    skip-cert-verify: true\n"
    )
    cache = CacheFiles(tmp_path)
    digest = cache.publish_raw(raw_yaml.encode("utf-8"), {})
    raw_url = f"http://clashsub:8080/raw/{digest}"
    payload = (
        "[General]\nloglevel = notify\n"
        "[Proxy]\n"
        "🇭🇰 香港-实验线路 BGP = vmess, node.example.test, 32521, username=abc, tls=false, ws=true, ws-path=/, sni=node.example.test, udp-relay=true\n"
        "🇭🇰 香港-广东专线 BGP 1 = trojan, node.example.test, 32443, password=xyz, sni=spoof.example.test, udp-relay=true\n"
        "[Rule]\nFINAL,DIRECT\n"
    )
    service = ConverterService(
        cache,
        "http://subconverter:25500",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )

    rendered = await service.render(
        "00000000-0000-0000-0000-000000000011", raw_url, "surge", source_digest=digest
    )

    assert "ws-path=/secret-path" in rendered
    assert "ws-headers=Host:spoof.example.test" in rendered
    assert "skip-cert-verify=true" in rendered


@pytest.mark.asyncio
async def test_surge_skips_node_injection_when_source_missing(tmp_path):
    payload = (
        "[General]\nloglevel = notify\n"
        "[Proxy]\nNode = ss, example.test, 443\n"
        "[Rule]\nFINAL,DIRECT\n"
    )
    service = ConverterService(
        CacheFiles(tmp_path),
        "http://subconverter:25500",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload)),
    )

    rendered = await service.render(
        "00000000-0000-0000-0000-000000000012", "http://clashsub:8080/raw/not-cached", "surge"
    )

    assert "Node = ss, example.test, 443" in rendered
    assert "ws-path=" not in rendered
    assert "skip-cert-verify" not in rendered

import asyncio
import base64
import gzip
import time

import httpx
import pytest

from clashsub.cache_files import CacheFiles
from clashsub.db import Database
from clashsub.sources import StaticUrlSource
from clashsub.subscription import InvalidSubscription, validate_subscription
from clashsub.subscription import UpstreamRefresher
from pydantic import SecretStr


PUBLIC_TEST_IP = "93.184.216.34"


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, payload: bytes):
        self.payload = payload
        self.started = False
        self.closed = False

    async def __aiter__(self):
        self.started = True
        yield self.payload

    async def aclose(self) -> None:
        self.closed = True


async def _public_resolver(hostname: str, port: int) -> tuple[str, ...]:
    assert hostname
    assert port == 443
    return (PUBLIC_TEST_IP,)


def _refresher(
    tmp_path,
    url: str,
    transport,
    resolver=_public_resolver,
    allowed_download_cidrs=(),
):
    db = Database(tmp_path / "state.db")
    db.initialize()
    return UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        (StaticUrlSource(SecretStr(url)),),
        transport=transport,
        resolver=resolver,
        allowed_download_cidrs=allowed_download_cidrs,
    )


@pytest.mark.parametrize(
    ("payload", "expected_format", "expected_count"),
    [
        (base64.b64encode(b"trojan://pass@node.example:443#one\nvmess://abc\n"), "base64", 2),
        (b"vless://id@node.example:443#one\ntuic://id:pass@node.example:443#two\n", "uri-list", 2),
        (b"proxies:\n  - {name: one, type: trojan, server: node.example, port: 443, password: pass}\n", "yaml", 1),
    ],
)
def test_validate_supported_formats(payload, expected_format, expected_count):
    result = validate_subscription(payload, 8 * 1024 * 1024)
    assert (result.content_format, result.node_count, result.payload) == (expected_format, expected_count, payload)


@pytest.mark.parametrize("payload", [b"<html>login</html>", b"Just a moment... Cloudflare", b"hello world", b""])
def test_rejects_pages_and_empty_node_sets(payload):
    with pytest.raises(InvalidSubscription):
        validate_subscription(payload, 1024)


def test_rejects_oversized_payload():
    with pytest.raises(InvalidSubscription, match="too large"):
        validate_subscription(b"x" * 11, 10)


def test_valid_subscription_may_contain_cloudflare_text():
    payload = (
        b"proxies:\n"
        b"  - {name: Cloudflare, type: trojan, server: node.example, "
        b"port: 443, password: pass}\n"
    )

    result = validate_subscription(payload, 1024)

    assert result.node_count == 1


@pytest.mark.asyncio
async def test_refresh_success_switches_digest_and_failure_keeps_it(tmp_path):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    responses = [
        httpx.Response(200, content=good, headers={"profile-update-interval": "24", "set-cookie": "drop=1"}),
        httpx.Response(502, content=b"bad"),
    ]
    transport = httpx.MockTransport(lambda request: responses.pop(0))
    db = Database(tmp_path / "state.db")
    db.initialize()
    refresher = UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        (StaticUrlSource(SecretStr("https://provider.invalid/sub?token=hidden")),),
        transport=transport,
        resolver=_public_resolver,
    )

    first = await refresher.refresh()
    second = await refresher.refresh()

    assert first.updated is True
    assert second.updated is False
    assert second.current_digest == first.current_digest
    assert second.consecutive_failures == 1
    assert CacheFiles(tmp_path / "cache").read_raw(first.current_digest).safe_headers == {
        "profile-update-interval": "24"
    }


@pytest.mark.asyncio
async def test_refresh_prunes_old_digests_and_invalidates_converted_templates(tmp_path):
    payloads = [
        base64.b64encode(f"trojan://pass@node.example:44{index}#node{index}\n".encode())
        for index in range(4)
    ]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payloads.pop(0)))
    db = Database(tmp_path / "state.db")
    db.initialize()
    cache = CacheFiles(tmp_path / "cache")
    refresher = UpstreamRefresher(
        db,
        cache,
        (StaticUrlSource(SecretStr("https://provider.invalid/sub?token=hidden")),),
        transport=transport,
        resolver=_public_resolver,
    )
    cache.write_converter_template("00000000-0000-0000-0000-000000000001", "stale", "clash")

    results = [await refresher.refresh() for _ in range(4)]

    assert all(result.updated for result in results)
    digests = [result.current_digest for result in results]
    remaining = {path.stem for path in (tmp_path / "cache" / "raw").glob("*.bin")}
    assert digests[0] not in remaining
    assert set(digests[1:]) <= remaining
    assert not list((tmp_path / "cache" / "converted").iterdir())


@pytest.mark.asyncio
async def test_refresh_if_stale_skips_fresh_and_refreshes_stale(tmp_path):
    payload = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(200, content=payload)

    db = Database(tmp_path / "state.db")
    db.initialize()
    refresher = UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        (StaticUrlSource(SecretStr("https://provider.invalid/sub?token=hidden")),),
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    )
    await refresher.refresh()

    assert await refresher.refresh_if_stale(3600) is None
    assert calls["count"] == 1

    with db.connect() as conn:
        conn.execute(
            "UPDATE runtime_state SET last_success_at=?, last_attempt_at=?",
            (time.time() - 7200, time.time() - 7200),
        )
    result = await refresher.refresh_if_stale(3600)

    assert result is not None and result.updated is True
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_refresh_if_stale_backs_off_after_consecutive_failures(tmp_path):
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(502, content=b"bad")

    db = Database(tmp_path / "state.db")
    db.initialize()
    refresher = UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        (StaticUrlSource(SecretStr("https://provider.invalid/sub?token=hidden")),),
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    )
    await refresher.refresh()
    await refresher.refresh()
    assert db.runtime_state()["consecutive_failures"] == 2

    with db.connect() as conn:
        conn.execute("UPDATE runtime_state SET last_success_at=?", (time.time() - 10000,))
    result = await refresher.refresh_if_stale(3600)

    assert result is None
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_refresh_if_stale_skips_while_refresh_in_progress(tmp_path):
    payload = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=payload)

    db = Database(tmp_path / "state.db")
    db.initialize()
    refresher = UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        (StaticUrlSource(SecretStr("https://provider.invalid/sub?token=hidden")),),
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    )
    refresher._refreshing = True

    assert await refresher.refresh_if_stale(3600) is None
    assert calls == []


@pytest.mark.asyncio
async def test_refresh_if_stale_does_not_retry_within_min_interval(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(502, content=b"bad")

    db = Database(tmp_path / "state.db")
    db.initialize()
    refresher = UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        (StaticUrlSource(SecretStr("https://provider.invalid/sub?token=hidden")),),
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    )
    await refresher.refresh()
    with db.connect() as conn:
        conn.execute("UPDATE runtime_state SET last_success_at=?", (time.time() - 7200,))

    result = await refresher.refresh_if_stale(3600)

    assert result is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_concurrent_refresh_if_stale_triggers_single_refresh(tmp_path):
    payload = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=payload)

    db = Database(tmp_path / "state.db")
    db.initialize()
    refresher = UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        (StaticUrlSource(SecretStr("https://provider.invalid/sub?token=hidden")),),
        transport=httpx.MockTransport(handler),
        resolver=_public_resolver,
    )
    await refresher.refresh()
    with db.connect() as conn:
        conn.execute(
            "UPDATE runtime_state SET last_success_at=?, last_attempt_at=?",
            (time.time() - 7200, time.time() - 7200),
        )
    calls.clear()

    results = await asyncio.gather(*[refresher.refresh_if_stale(3600) for _ in range(5)])

    assert sum(result is not None for result in results) == 1
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/sub",
        "https://10.0.0.1/sub",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/sub",
        "https://[fd00::1]/sub",
        "https://[fe80::1]/sub",
        "https://[::ffff:127.0.0.1]/sub",
    ],
)
async def test_download_rejects_non_global_ip_destinations(tmp_path, url):
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("unsafe destination must be rejected before the request")

    result = await _refresher(tmp_path, url, httpx.MockTransport(unexpected_request)).refresh()

    assert result.updated is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [
        (),
        ("127.0.0.1",),
        ("10.0.0.1",),
    ],
)
async def test_download_rejects_domains_with_no_allowed_dns_answer(tmp_path, addresses):
    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        assert (hostname, port) == ("public.example.test", 443)
        return addresses

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("unsafe DNS answer must be rejected before the request")

    result = await _refresher(
        tmp_path,
        "https://public.example.test/sub",
        httpx.MockTransport(unexpected_request),
        resolver,
    ).refresh()

    assert result.updated is False


@pytest.mark.asyncio
async def test_download_filters_mixed_dns_answers_to_allowed_only(tmp_path):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    requested_hosts: list[str] = []

    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        assert (hostname, port) == ("public.example.test", 443)
        return (PUBLIC_TEST_IP, "169.254.169.254")

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(200, content=good)

    result = await _refresher(
        tmp_path,
        "https://public.example.test/sub",
        httpx.MockTransport(handler),
        resolver,
    ).refresh()

    assert result.updated is True
    assert requested_hosts == [PUBLIC_TEST_IP]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "http://next.example.test/sub",
        "https://127.0.0.1/sub",
        "https://169.254.169.254/latest/meta-data",
    ],
)
async def test_download_rejects_unsafe_redirect_destinations(tmp_path, location):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"https://{request.headers['host']}{request.url.raw_path.decode()}")
        return httpx.Response(302, headers={"location": location})

    result = await _refresher(
        tmp_path,
        "https://origin.example.test/sub",
        httpx.MockTransport(handler),
    ).refresh()

    assert result.updated is False
    assert seen == ["https://origin.example.test/sub"]


@pytest.mark.asyncio
async def test_download_resolves_dns_again_for_each_redirect_hop(tmp_path):
    resolutions = iter(((PUBLIC_TEST_IP,), ("127.0.0.1",)))
    resolved_hosts: list[str] = []
    seen: list[str] = []

    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        resolved_hosts.append(hostname)
        return next(resolutions)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"https://{request.headers['host']}{request.url.raw_path.decode()}")
        return httpx.Response(302, headers={"location": "/next"})

    result = await _refresher(
        tmp_path,
        "https://rebind.example.test/start",
        httpx.MockTransport(handler),
        resolver,
    ).refresh()

    assert result.updated is False
    assert resolved_hosts == ["rebind.example.test", "rebind.example.test"]
    assert seen == ["https://rebind.example.test/start"]


@pytest.mark.asyncio
async def test_download_follows_safe_https_redirect(tmp_path):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    resolved_hosts: list[str] = []
    seen: list[str] = []

    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        resolved_hosts.append(hostname)
        return (PUBLIC_TEST_IP,)

    def handler(request: httpx.Request) -> httpx.Response:
        logical_url = f"https://{request.headers['host']}{request.url.raw_path.decode()}"
        seen.append(logical_url)
        if request.headers["host"] == "origin.example.test":
            return httpx.Response(302, headers={"location": "https://cdn.example.test/sub"})
        return httpx.Response(200, content=good)

    result = await _refresher(
        tmp_path,
        "https://origin.example.test/start",
        httpx.MockTransport(handler),
        resolver,
    ).refresh()

    assert result.updated is True
    assert resolved_hosts == ["origin.example.test", "cdn.example.test"]
    assert seen == [
        "https://origin.example.test/start",
        "https://cdn.example.test/sub",
    ]


@pytest.mark.asyncio
async def test_download_connects_to_validated_ip_and_preserves_idna_host_and_sni(tmp_path):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    resolved_hosts: list[str] = []

    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        resolved_hosts.append(hostname)
        return (PUBLIC_TEST_IP,)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == PUBLIC_TEST_IP
        assert request.headers["host"] == "xn--fa-hia.example.test"
        assert request.headers["accept-encoding"] == "identity"
        assert request.extensions["sni_hostname"] == "xn--fa-hia.example.test"
        return httpx.Response(200, content=good)

    result = await _refresher(
        tmp_path,
        "https://faß.example.test/sub",
        httpx.MockTransport(handler),
        resolver,
    ).refresh()

    assert result.updated is True
    assert resolved_hosts == ["xn--fa-hia.example.test"]


@pytest.mark.asyncio
async def test_download_allows_explicit_openclash_fake_ip_cidr(tmp_path):
    fake_ip = "198.18.13.202"
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")

    async def resolver(hostname: str, port: int) -> tuple[str, ...]:
        return (fake_ip,)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == fake_ip
        assert request.headers["host"] == "provider.example.test"
        return httpx.Response(200, content=good)

    result = await _refresher(
        tmp_path,
        "https://provider.example.test/sub",
        httpx.MockTransport(handler),
        resolver,
        allowed_download_cidrs=("198.18.0.0/15",),
    ).refresh()

    assert result.updated is True


@pytest.mark.asyncio
async def test_download_rejects_encoded_response_without_decompression(tmp_path):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    encoded = TrackingStream(gzip.compress(good))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=encoded,
        )

    result = await _refresher(
        tmp_path,
        "https://provider.example.test/sub",
        httpx.MockTransport(handler),
    ).refresh()

    assert result.updated is False
    assert encoded.started is False
    assert encoded.closed is True


@pytest.mark.asyncio
async def test_download_stops_after_three_redirects(tmp_path):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        next_path = "/b" if request.url.path == "/a" else "/a"
        return httpx.Response(302, headers={"location": next_path})

    result = await _refresher(
        tmp_path,
        "https://loop.example.test/a",
        httpx.MockTransport(handler),
    ).refresh()

    assert result.updated is False
    assert len(seen) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://public.example.test/sub",
        "https://user:password@public.example.test/sub",
        "https://public.example.test/sub#fragment",
        "https://public.example.test/sub\tpath",
        "https://public.example.test:bad/sub",
    ],
)
async def test_download_rejects_malformed_or_ambiguous_urls(tmp_path, url):
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("unsafe URL must be rejected before the request")

    result = await _refresher(tmp_path, url, httpx.MockTransport(unexpected_request)).refresh()

    assert result.updated is False

import base64
import json

import httpx
import pytest
from pydantic import SecretStr

from clashsub.cache_files import CacheFiles
from clashsub.db import Database
from clashsub.sources import V2BoardSubscriptionSource, ResolvedSubscription, StaticUrlSource
from clashsub.subscription import UpstreamRefresher
from clashsub.v2board_client import V2BoardClient, V2BoardError, V2BoardSubscription


async def _public_resolver(hostname: str, port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


class FakeSource:
    def __init__(self, name, results):
        self.name = name
        self.results = list(results)
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _resolved(source: str, url: str) -> ResolvedSubscription:
    return ResolvedSubscription(
        source,
        SecretStr(url),
        expires_at=1_900_000_000 if source == "protocol" else None,
        login_succeeded_at=10 if source == "protocol" else None,
        resolved_at=11 if source == "protocol" else None,
        user_agent="BBGen2UA" if source == "protocol" else "clash.meta",
    )


def _refresher(tmp_path, sources, transport, resolver=_public_resolver):
    db = Database(tmp_path / "state.db")
    db.initialize()
    return db, UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        tuple(sources),
        transport=transport,
        resolver=resolver,
    )


@pytest.mark.asyncio
async def test_protocol_success_skips_fallback_and_records_source(tmp_path):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    protocol = FakeSource("protocol", [_resolved("protocol", "https://sub.invalid/one")])
    fallback = FakeSource("fallback", [_resolved("fallback", "https://sub.invalid/fallback")])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == "BBGen2UA"
        return httpx.Response(200, content=good)

    db, refresher = _refresher(tmp_path, (protocol, fallback), httpx.MockTransport(handler))

    result = await refresher.refresh()

    assert result.updated is True
    assert result.source == "protocol"
    assert protocol.calls == 1
    assert fallback.calls == 0
    state = db.runtime_state()
    assert state["last_success_source"] == "protocol"
    assert state["protocol_last_login_at"] == 10
    assert state["protocol_last_subscribe_at"] == 11
    assert state["protocol_subscription_expires_at"] == 1_900_000_000


@pytest.mark.asyncio
async def test_invalid_protocol_payload_uses_fallback_and_keeps_protocol_error(tmp_path):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    protocol = FakeSource("protocol", [_resolved("protocol", "https://sub.invalid/bad")])
    fallback = FakeSource("fallback", [_resolved("fallback", "https://sub.invalid/fallback")])
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers["user-agent"]))
        if request.url.path == "/bad":
            return httpx.Response(200, content=b"not a subscription")
        return httpx.Response(200, content=good)

    db, refresher = _refresher(tmp_path, (protocol, fallback), httpx.MockTransport(handler))

    result = await refresher.refresh()

    assert result.updated is True
    assert result.source == "fallback"
    assert seen == [("/bad", "BBGen2UA"), ("/fallback", "clash.meta")]
    state = db.runtime_state()
    assert state["last_success_source"] == "fallback"
    assert state["consecutive_failures"] == 0
    assert state["protocol_last_error_category"] == "invalid_subscription"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "redirect_target",
    [
        "http://cdn.example.test/final",
        "https://127.0.0.1/internal",
    ],
)
async def test_protocol_download_rejects_unsafe_redirects_and_uses_fallback(
    tmp_path,
    redirect_target,
):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    protocol = FakeSource("protocol", [_resolved("protocol", "https://sub.invalid/start")])
    fallback = FakeSource("fallback", [_resolved("fallback", "https://sub.invalid/fallback")])
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"https://{request.headers['host']}{request.url.raw_path.decode()}")
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": redirect_target})
        if request.url.path == "/fallback":
            return httpx.Response(200, content=good)
        raise AssertionError("unsafe redirect target was requested")

    db, refresher = _refresher(
        tmp_path,
        (protocol, fallback),
        httpx.MockTransport(handler),
    )

    result = await refresher.refresh()

    assert result.source == "fallback"
    assert seen == ["https://sub.invalid/start", "https://sub.invalid/fallback"]
    assert db.runtime_state()["protocol_last_error_category"] == "invalid_subscription"


@pytest.mark.asyncio
async def test_protocol_invalid_url_error_uses_fallback(tmp_path):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")

    def invalid_api_request(request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL("bad URL")

    protocol = V2BoardSubscriptionSource(
        V2BoardClient(
            "https://panel.example.test/api/v1",
            SecretStr("member@example.test"),
            SecretStr("airport-pass"),
            transport=httpx.MockTransport(invalid_api_request),
        )
    )
    fallback = FakeSource("fallback", [_resolved("fallback", "https://sub.invalid/fallback")])

    db, refresher = _refresher(
        tmp_path,
        (protocol, fallback),
        httpx.MockTransport(lambda request: httpx.Response(200, content=good)),
    )

    result = await refresher.refresh()

    assert result.source == "fallback"
    assert db.runtime_state()["protocol_last_error_category"] == "network"


@pytest.mark.asyncio
async def test_all_sources_fail_once_and_preserve_last_valid_cache(tmp_path):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    protocol = FakeSource(
        "protocol",
        [
            _resolved("protocol", "https://sub.invalid/good"),
            V2BoardError("network", "login"),
        ],
    )
    fallback = FakeSource("fallback", [_resolved("fallback", "https://sub.invalid/fail")])
    responses = [httpx.Response(200, content=good), httpx.Response(502, content=b"unavailable")]
    db, refresher = _refresher(
        tmp_path,
        (protocol, fallback),
        httpx.MockTransport(lambda request: responses.pop(0)),
    )

    first = await refresher.refresh()
    second = await refresher.refresh()

    assert first.updated is True
    assert second.updated is False
    assert second.source is None
    assert second.current_digest == first.current_digest
    assert second.consecutive_failures == 1
    assert db.runtime_state()["protocol_last_error_category"] == "network"


@pytest.mark.asyncio
async def test_protocol_download_auth_failure_re_resolves_only_once(tmp_path):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    protocol = FakeSource(
        "protocol",
        [
            _resolved("protocol", "https://sub.invalid/stale"),
            _resolved("protocol", "https://sub.invalid/fresh"),
        ],
    )
    fallback = FakeSource("fallback", [_resolved("fallback", "https://sub.invalid/fallback")])
    responses = [httpx.Response(403, content=b"denied"), httpx.Response(200, content=good)]
    db, refresher = _refresher(
        tmp_path,
        (protocol, fallback),
        httpx.MockTransport(lambda request: responses.pop(0)),
    )

    result = await refresher.refresh()

    assert result.updated is True
    assert result.source == "protocol"
    assert protocol.calls == 2
    assert fallback.calls == 0
    assert db.runtime_state()["consecutive_failures"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_persistent_protocol_auth_failure_stops_after_one_retry(tmp_path, status_code):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    protocol = FakeSource(
        "protocol",
        [
            _resolved("protocol", "https://sub.invalid/stale"),
            _resolved("protocol", "https://sub.invalid/still-stale"),
        ],
    )
    fallback = FakeSource("fallback", [_resolved("fallback", "https://sub.invalid/fallback")])
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers["user-agent"]))
        if request.url.path == "/fallback":
            return httpx.Response(200, content=good)
        return httpx.Response(status_code, content=b"denied")

    db, refresher = _refresher(
        tmp_path,
        (protocol, fallback),
        httpx.MockTransport(handler),
    )

    result = await refresher.refresh()

    assert result.updated is True
    assert result.source == "fallback"
    assert protocol.calls == 2
    assert fallback.calls == 1
    assert seen == [
        ("/stale", "BBGen2UA"),
        ("/still-stale", "BBGen2UA"),
        ("/fallback", "clash.meta"),
    ]
    state = db.runtime_state()
    assert state["last_success_source"] == "fallback"
    assert state["consecutive_failures"] == 0
    assert state["protocol_last_error_category"] == "authentication"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body", "content_type", "category"),
    [
        (
            403,
            {"message": "captcha required response-secret-marker"},
            "application/json",
            "captcha_required",
        ),
        (
            200,
            {"message": "Turnstile required", "detail": "response-secret-marker"},
            "application/json",
            "captcha_required",
        ),
        (
            403,
            "<!doctype html><title>Just a moment</title> response-secret-marker",
            "text/html",
            "challenge",
        ),
        (
            200,
            "Cloudflare cf-chl-response-secret-marker",
            "text/plain",
            "challenge",
        ),
    ],
)
async def test_protocol_download_interstitial_uses_fallback_without_auth_retry(
    tmp_path,
    status_code,
    body,
    content_type,
    category,
):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    protocol = FakeSource(
        "protocol",
        [
            _resolved("protocol", "https://sub.invalid/challenge"),
            _resolved("protocol", "https://sub.invalid/retry-must-not-run"),
        ],
    )
    fallback = FakeSource("fallback", [_resolved("fallback", "https://sub.invalid/fallback")])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fallback":
            return httpx.Response(200, content=good)
        if isinstance(body, dict):
            return httpx.Response(status_code, content=json.dumps(body).encode(), headers={"content-type": content_type})
        return httpx.Response(status_code, text=body, headers={"content-type": content_type})

    db, refresher = _refresher(
        tmp_path,
        (protocol, fallback),
        httpx.MockTransport(handler),
    )

    result = await refresher.refresh()

    assert result.updated is True
    assert result.source == "fallback"
    assert protocol.calls == 1
    assert fallback.calls == 1
    assert db.runtime_state()["protocol_last_error_category"] == category
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"response-secret-marker" not in path.read_bytes()


@pytest.mark.asyncio
async def test_invalid_url_from_download_transport_uses_fallback_with_stable_error(tmp_path):
    good = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    protocol = FakeSource("protocol", [_resolved("protocol", "https://sub.invalid/invalid")])
    fallback = FakeSource("fallback", [_resolved("fallback", "https://sub.invalid/fallback")])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/invalid":
            raise httpx.InvalidURL("response-secret-marker")
        return httpx.Response(200, content=good)

    db, refresher = _refresher(
        tmp_path,
        (protocol, fallback),
        httpx.MockTransport(handler),
    )

    result = await refresher.refresh()

    assert result.updated is True
    assert result.source == "fallback"
    assert db.runtime_state()["protocol_last_error_category"] == "invalid_subscription"
    assert b"response-secret-marker" not in (tmp_path / "state.db").read_bytes()


@pytest.mark.asyncio
async def test_protocol_test_resolves_without_downloading_or_changing_cache(tmp_path):
    protocol = FakeSource("protocol", [_resolved("protocol", "https://sub.invalid/one")])

    def unexpected_download(request: httpx.Request) -> httpx.Response:
        raise AssertionError("protocol test must not download the subscription")

    db, refresher = _refresher(
        tmp_path,
        (protocol,),
        httpx.MockTransport(unexpected_download),
    )

    result = await refresher.test_protocol()

    assert result.ok is True
    assert result.error_category is None
    assert result.expires_at == 1_900_000_000
    assert db.runtime_state()["current_digest"] is None


@pytest.mark.asyncio
async def test_static_url_source_hides_token_and_uses_standard_user_agent():
    source = StaticUrlSource(SecretStr("https://sub.invalid/path?token=hidden"))

    result = await source.fetch()

    assert result.source == "fallback"
    assert result.user_agent == "clash.meta"
    assert result.url.get_secret_value().endswith("token=hidden")
    assert "hidden" not in repr(source)
    assert "hidden" not in repr(result)


@pytest.mark.asyncio
async def test_protocol_source_preserves_resolved_metadata_and_user_agent():
    class FakeClient:
        async def fetch_subscription(self):
            return V2BoardSubscription(
                SecretStr("https://sub.invalid/path?token=hidden&flag=clash"),
                1_900_000_000,
                10,
                11,
            )

    result = await V2BoardSubscriptionSource(FakeClient()).fetch()

    assert result.source == "protocol"
    assert result.expires_at == 1_900_000_000
    assert result.login_succeeded_at == 10
    assert result.resolved_at == 11
    assert result.user_agent == "BBGen2UA"
    assert "hidden" not in repr(result)

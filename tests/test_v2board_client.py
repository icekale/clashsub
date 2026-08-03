import gzip
import json

import httpx
import pytest
from pydantic import SecretStr

from clashsub.v2board_client import V2BoardClient, V2BoardError


class OversizedJsonStream(httpx.AsyncByteStream):
    def __init__(self, prefix: bytes):
        self.prefix = prefix
        self.read_past_limit = False

    async def __aiter__(self):
        yield self.prefix
        yield b"x" * (64 * 1024)
        self.read_past_limit = True
        yield b'"}'

    async def aclose(self) -> None:
        pass


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


def _client(transport: httpx.AsyncBaseTransport) -> V2BoardClient:
    return V2BoardClient(
        "https://panel.example.test/api/v1",
        SecretStr("member@example.test"),
        SecretStr("airport-pass"),
        transport=transport,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://sub.example.test/token", "https://sub.example.test/token?flag=clash"),
        (" https://sub.example.test/token ", "https://sub.example.test/token?flag=clash"),
        (
            "https://sub.example.test/token?target=raw",
            "https://sub.example.test/token?target=raw&flag=clash",
        ),
        (
            "https://sub.example.test/token?sig=a%2Fb%20c&x=%7E&x=again#ignored",
            "https://sub.example.test/token?sig=a%2Fb%20c&x=%7E&x=again&flag=clash",
        ),
    ],
)
async def test_login_and_subscription_match_native_client(url: str, expected: str):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["user-agent"] == "BBGen2UA"
        assert request.headers["accept-encoding"] == "identity"
        if request.url.path.endswith("/passport/auth/login"):
            assert json.loads(request.content) == {
                "email": "member@example.test",
                "password": "airport-pass",
            }
            return httpx.Response(200, json={"data": {"auth_data": "native-auth", "token": "unused"}})
        assert request.url.path.endswith("/user/getSubscribe")
        assert request.headers["authorization"] == "native-auth"
        assert not request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(
            200,
            json={"data": {"subscribe_url": url, "expired_at": "1800000000"}},
        )

    result = await _client(httpx.MockTransport(handler)).fetch_subscription()

    assert result.url.get_secret_value() == expected
    assert result.expires_at == 1_800_000_000
    assert result.user_agent == "BBGen2UA"
    assert result.login_succeeded_at <= result.resolved_at
    assert len(requests) == 2
    assert "native-auth" not in repr(result)
    assert expected not in repr(result)


@pytest.mark.asyncio
async def test_auth_data_is_forwarded_exactly():
    auth_data = " native-auth "

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/passport/auth/login"):
            return httpx.Response(200, json={"data": {"auth_data": auth_data}})
        assert request.headers["authorization"] == auth_data
        return httpx.Response(
            200,
            json={"data": {"subscribe_url": "https://sub.example.test/token"}},
        )

    await _client(httpx.MockTransport(handler)).fetch_subscription()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["login", "subscribe"])
async def test_valid_json_with_unrelated_cloudflare_text_is_accepted(stage):
    def handler(request: httpx.Request) -> httpx.Response:
        is_login = request.url.path.endswith("/passport/auth/login")
        if is_login:
            document = {"data": {"auth_data": "native-auth"}}
        else:
            document = {"data": {"subscribe_url": "https://sub.example.test/token"}}
        if (stage == "login") == is_login:
            document["edge_provider"] = "cloudflare"
        return httpx.Response(200, json=document)

    result = await _client(httpx.MockTransport(handler)).fetch_subscription()

    assert result.url.get_secret_value() == "https://sub.example.test/token?flag=clash"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["login", "subscribe"])
async def test_oversized_json_response_is_rejected_before_stream_is_exhausted(stage):
    prefix = (
        b'{"data":{"auth_data":"native-auth"},"padding":"'
        if stage == "login"
        else b'{"data":{"subscribe_url":"https://sub.example.test/token"},"padding":"'
    )
    oversized = OversizedJsonStream(prefix)

    def handler(request: httpx.Request) -> httpx.Response:
        is_login = request.url.path.endswith("/passport/auth/login")
        if (stage == "login") == is_login:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=oversized,
            )
        return httpx.Response(200, json={"data": {"auth_data": "native-auth"}})

    with pytest.raises(V2BoardError) as caught:
        await _client(httpx.MockTransport(handler)).fetch_subscription()

    assert caught.value.category == "invalid_response"
    assert caught.value.stage == stage
    assert oversized.read_past_limit is False


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["login", "subscribe"])
async def test_encoded_api_response_is_rejected_without_decompression(stage):
    document = (
        {"data": {"auth_data": "native-auth"}}
        if stage == "login"
        else {"data": {"subscribe_url": "https://sub.example.test/token"}}
    )
    encoded = TrackingStream(gzip.compress(json.dumps(document).encode()))

    def handler(request: httpx.Request) -> httpx.Response:
        is_login = request.url.path.endswith("/passport/auth/login")
        if (stage == "login") == is_login:
            return httpx.Response(
                200,
                headers={"content-encoding": "gzip"},
                stream=encoded,
            )
        return httpx.Response(200, json={"data": {"auth_data": "native-auth"}})

    with pytest.raises(V2BoardError) as caught:
        await _client(httpx.MockTransport(handler)).fetch_subscription()

    assert caught.value.category == "invalid_response"
    assert caught.value.stage == stage
    assert encoded.started is False
    assert encoded.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "category"),
    [
        (httpx.Response(200, text="<!doctype html><title>Just a moment</title>"), "challenge"),
        (httpx.Response(403, text="<!doctype html><title>Just a moment</title>"), "challenge"),
        (httpx.Response(200, json={"code": 403, "message": "请完成人机验证"}), "captcha_required"),
        (httpx.Response(403, json={"message": "captcha required"}), "captcha_required"),
        (httpx.Response(403, json={"message": "reCAPTCHA required"}), "captcha_required"),
        (httpx.Response(422, json={"message": "Turnstile required"}), "captcha_required"),
        (httpx.Response(422, json={"message": "Cloudflare Turnstile required"}), "captcha_required"),
        (httpx.Response(422, json={"message": "请完成人机验证"}), "captcha_required"),
        (httpx.Response(200, json={"data": {}}), "invalid_response"),
        (httpx.Response(401, text="denied"), "authentication"),
        (httpx.Response(403, json={"message": "denied"}), "authentication"),
        (httpx.Response(401, json={"message": "身份验证失败"}), "authentication"),
        (httpx.Response(403, json={"message": "身份验证失败"}), "authentication"),
        (httpx.Response(503, text="unavailable"), "http_error"),
        (httpx.Response(200, text="not-json"), "invalid_response"),
    ],
)
async def test_login_failures_are_stable_and_redacted(
    response: httpx.Response,
    category: str,
):
    transport = httpx.MockTransport(lambda request: response)

    with pytest.raises(V2BoardError) as caught:
        await _client(transport).fetch_subscription()

    assert caught.value.category == category
    assert caught.value.stage == "login"
    assert "member@example.test" not in str(caught.value)
    assert "airport-pass" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "category"),
    [
        ({}, "invalid_response"),
        ({"subscribe_url": " \t "}, "invalid_response"),
        ({"subscribe_url": "http://sub.example.test/token"}, "invalid_response"),
        ({"subscribe_url": "https:///token"}, "invalid_response"),
        ({"subscribe_url": "https://[broken/token"}, "invalid_response"),
        ({"subscribe_url": "https://sub.example.test:bad/token"}, "invalid_response"),
        ({"subscribe_url": "https://sub.example.test:65536/token"}, "invalid_response"),
        ({"subscribe_url": "https://sub example.test/token"}, "invalid_response"),
        ({"subscribe_url": "https://@sub.example.test/token"}, "invalid_response"),
        ({"subscribe_url": "https://:@sub.example.test/token"}, "invalid_response"),
        (
            {"subscribe_url": "https://sub.example.test/token", "expired_at": "tomorrow"},
            "invalid_response",
        ),
        (
            {"subscribe_url": "https://sub.example.test/token", "message": "turnstile required"},
            "captcha_required",
        ),
    ],
)
async def test_subscription_response_validation(data: dict, category: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/passport/auth/login"):
            return httpx.Response(200, json={"data": {"auth_data": "native-auth"}})
        if "message" in data:
            return httpx.Response(200, json={"message": data["message"], "data": data})
        return httpx.Response(200, json={"data": data})

    with pytest.raises(V2BoardError) as caught:
        await _client(httpx.MockTransport(handler)).fetch_subscription()

    assert caught.value.category == category
    assert caught.value.stage == "subscribe"


@pytest.mark.asyncio
async def test_subscribe_authentication_error_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/passport/auth/login"):
            return httpx.Response(200, json={"data": {"auth_data": "native-auth"}})
        return httpx.Response(403, text="denied")

    with pytest.raises(V2BoardError) as caught:
        await _client(httpx.MockTransport(handler)).fetch_subscription()

    assert caught.value.category == "authentication"
    assert caught.value.stage == "subscribe"
    assert caught.value.retry_auth is True


@pytest.mark.asyncio
async def test_network_error_is_redacted():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("member@example.test airport-pass", request=request)

    with pytest.raises(V2BoardError) as caught:
        await _client(httpx.MockTransport(handler)).fetch_subscription()

    assert caught.value.category == "network"
    assert caught.value.stage == "login"
    assert str(caught.value) == "network"

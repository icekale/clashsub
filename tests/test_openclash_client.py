import httpx
import pytest

from clashsub.openclash_client import OpenClashClient, OpenClashError


@pytest.mark.asyncio
async def test_version_returns_payload():
    client = OpenClashClient(
        "http://192.168.1.1:9090/",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"meta": True, "version": "alpha"})
        ),
    )
    assert await client.version() == {"meta": True, "version": "alpha"}


@pytest.mark.asyncio
async def test_refresh_provider_sends_force_put_with_bearer():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"updatedAt": "now"})

    client = OpenClashClient(
        "http://192.168.1.1:9090",
        "top-secret",
        transport=httpx.MockTransport(handler),
    )
    result = await client.refresh_provider("Provider_988009")
    assert result == {"updatedAt": "now"}
    assert seen == {
        "method": "PUT",
        "url": "http://192.168.1.1:9090/providers/proxies/Provider_988009?force=true",
        "auth": "Bearer top-secret",
    }


@pytest.mark.asyncio
async def test_unauthorized_raises_openclash_error():
    client = OpenClashClient(
        "http://192.168.1.1:9090",
        "wrong",
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="unauthorized")),
    )
    with pytest.raises(OpenClashError, match="unauthorized"):
        await client.version()


@pytest.mark.asyncio
async def test_network_error_raises_openclash_error():
    def handler(request):
        raise httpx.ConnectError("down")

    client = OpenClashClient(
        "http://192.168.1.1:9090",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OpenClashError, match="network"):
        await client.version()


@pytest.mark.asyncio
async def test_invalid_provider_name_rejected():
    client = OpenClashClient("http://192.168.1.1:9090", "secret")
    with pytest.raises(OpenClashError, match="provider"):
        await client.refresh_provider("bad/name")

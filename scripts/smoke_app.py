import base64

import httpx
import uvicorn

from clashsub.app import create_app
from clashsub.config import Settings


PAYLOAD = base64.b64encode(b"trojan://pass@node.example:443#one\n")
PUBLIC_TEST_IP = "93.184.216.34"


def fixture_transport(request: httpx.Request) -> httpx.Response:
    assert request.headers["host"] == "fixture.example.test"
    assert request.url.host == PUBLIC_TEST_IP
    return httpx.Response(200, content=PAYLOAD)


async def fixture_resolver(hostname: str, port: int) -> tuple[str, ...]:
    assert (hostname, port) == ("fixture.example.test", 443)
    return (PUBLIC_TEST_IP,)


if __name__ == "__main__":
    app = create_app(
        Settings.from_env(),
        transport=httpx.MockTransport(fixture_transport),
        resolver=fixture_resolver,
        start_scheduler=True,
    )
    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)

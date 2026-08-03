import base64
import json
from dataclasses import replace

import httpx
import pytest
from pydantic import SecretStr

from clashsub.cache_files import CacheFiles
from clashsub.db import Database
from clashsub.secret_store import SecretStore
from clashsub.sources import V2BoardSubscriptionSource
from clashsub.subscription import UpstreamRefresher
from clashsub.v2board_client import V2BoardClient


GOOD = base64.b64encode(b"trojan://pass@node.example:443#one\n")


def _key_file(tmp_path):
    path = tmp_path / "key"
    path.write_bytes(base64.b64encode(b"k" * 32))
    return path


@pytest.mark.asyncio
async def test_protocol_source_reads_a_fresh_credential_snapshot(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/passport/auth/login"):
            seen.append(json.loads(request.content)["password"])
            return httpx.Response(200, json={"data": {"auth_data": "auth"}})
        return httpx.Response(
            200,
            json={"data": {"subscribe_url": "https://sub.example.test/x"}},
        )

    current = ("member@example.test", "old-password")
    client = V2BoardClient(
        "https://panel.example.test/api/v1",
        SecretStr(current[0]),
        SecretStr(current[1]),
        transport=httpx.MockTransport(handler),
    )
    source = V2BoardSubscriptionSource(client, credential_provider=lambda: current)
    await source.fetch()
    current = ("member@example.test", "new-password")
    await source.fetch()

    assert seen == ["old-password", "new-password"]


@pytest.mark.asyncio
async def test_failed_candidate_keeps_previous_credentials_and_cache(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    store = SecretStore(db, _key_file(tmp_path))
    store.put("airport_credentials", json.dumps({"username": "old", "password": "old-pass"}))
    candidate_is_valid = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal candidate_is_valid
        if request.url.path.endswith("/passport/auth/login"):
            if not candidate_is_valid:
                return httpx.Response(403, json={"message": "invalid credentials"})
            return httpx.Response(200, json={"data": {"auth_data": "auth"}})
        if request.url.path.endswith("/user/getSubscribe"):
            return httpx.Response(
                200,
                json={"data": {"subscribe_url": "https://sub.example.test/x"}},
            )
        return httpx.Response(200, content=GOOD)

    source = V2BoardSubscriptionSource(
        V2BoardClient(
            "https://panel.example.test/api/v1",
            SecretStr("old"),
            SecretStr("old-pass"),
            transport=httpx.MockTransport(handler),
        ),
        credential_provider=lambda: ("old", "old-pass"),
    )
    refresher = UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        (source,),
        transport=httpx.MockTransport(handler),
        resolver=lambda hostname, port: _public_resolver(hostname, port),
        credential_store=store,
    )

    result = await refresher.update_protocol_credentials("candidate", "candidate-pass")

    assert result.ok is False
    assert result.error_category == "authentication"
    assert json.loads(store.get("airport_credentials")) == {"username": "old", "password": "old-pass"}
    assert db.runtime_state()["current_digest"] is None


async def _public_resolver(hostname, port):
    return ("93.184.216.34",)

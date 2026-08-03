import base64
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from clashsub.cache_files import CacheFiles
from clashsub.db import Database
from clashsub.events import configure_logging, read_recent_events
from clashsub.sources import StaticUrlSource
from clashsub.subscription import UpstreamRefresher


PUBLIC_TEST_IP = "93.184.216.34"


async def _public_resolver(hostname: str, port: int) -> tuple[str, ...]:
    assert hostname
    assert port == 443
    return (PUBLIC_TEST_IP,)


def _refresher(tmp_path: Path, transport):
    db = Database(tmp_path / "state.db")
    db.initialize()
    return UpstreamRefresher(
        db,
        CacheFiles(tmp_path / "cache"),
        (StaticUrlSource(SecretStr("https://provider.invalid/sub?token=hidden")),),
        transport=transport,
        resolver=_public_resolver,
    )


@pytest.mark.asyncio
async def test_refresh_success_writes_redacted_log(tmp_path: Path):
    log_path = tmp_path / "events.log"
    configure_logging(log_path)
    payload = base64.b64encode(b"trojan://pass@node.example:443#one\n")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payload))

    await _refresher(tmp_path, transport).refresh()

    lines = read_recent_events(log_path)
    assert any("refresh succeeded" in line and "source=fallback" in line for line in lines)
    assert not any("token=hidden" in line for line in lines)


@pytest.mark.asyncio
async def test_refresh_failure_writes_warning_log(tmp_path: Path):
    log_path = tmp_path / "events.log"
    configure_logging(log_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(502, content=b"bad"))

    await _refresher(tmp_path, transport).refresh()

    lines = read_recent_events(log_path)
    assert any("all subscription sources failed" in line for line in lines)

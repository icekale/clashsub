import asyncio
import time

import httpx
import pytest

from clashsub.cache_files import CacheFiles
from clashsub.db import Database
from clashsub.health import NodeHealthChecker, _resolve_via_doh


@pytest.mark.asyncio
async def test_health_check_records_online_and_offline(tmp_path):
    async def on_client(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(on_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        db = Database(tmp_path / "state.db")
        db.initialize()
        cache = CacheFiles(tmp_path / "cache")
        payload = (
            "proxies:\n"
            '  - name: "OK Node"\n'
            "    type: ss\n"
            "    server: 127.0.0.1\n"
            f"    port: {port}\n"
            '  - name: "Dead Node"\n'
            "    type: ss\n"
            "    server: 127.0.0.1\n"
            "    port: 1\n"
        ).encode()
        digest = cache.publish_raw(payload, {})
        db.record_refresh_success(digest, 2, "yaml", {}, time.time(), "test")

        async def resolver(hostname, port):
            return (hostname,)

        checker = NodeHealthChecker(db, cache, resolver=resolver, timeout_seconds=2)
        summary = await checker.run_once()

        assert summary.total == 2
        assert summary.online == 1
        rows = {row["name"]: row for row in db.list_node_health()}
        assert rows["OK Node"]["ok"] == 1
        assert rows["OK Node"]["latency_ms"] >= 0
        assert rows["Dead Node"]["ok"] == 0
        assert rows["Dead Node"]["latency_ms"] is None
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_health_check_without_cache_returns_empty(tmp_path):
    db = Database(tmp_path / "state.db")
    db.initialize()
    checker = NodeHealthChecker(db, CacheFiles(tmp_path / "cache"))
    summary = await checker.run_once()
    assert (summary.total, summary.online, summary.checked_at) == (0, 0, None)


@pytest.mark.asyncio
async def test_doh_resolver_extracts_a_records():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "Status": 0,
                "Answer": [
                    {"name": "host.example.", "type": 5, "data": "real.example."},
                    {"name": "real.example.", "type": 1, "data": "27.44.127.106"},
                    {"name": "real.example.", "type": 1, "data": "27.44.127.107"},
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        assert await _resolve_via_doh("host.example", client) == (
            "27.44.127.106",
            "27.44.127.107",
        )

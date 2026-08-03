def test_healthz_discloses_only_liveness(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_raw_returns_404_for_unknown_and_503_without_cache(client):
    assert client.get("/raw/not-a-share").status_code == 404
    created = client.app.state.services.shares.create("local-test")
    token = created.raw_url.rsplit("/", 1)[1]
    response = client.get(f"/raw/{token}")
    assert response.status_code == 503


def test_raw_preserves_bytes_and_only_safe_headers(client):
    services = client.app.state.services
    created = services.shares.create("local-test")
    token = created.raw_url.rsplit("/", 1)[1]
    digest = services.cache.publish_raw(
        b"exact-upstream-bytes",
        {"subscription-userinfo": "upload=1", "set-cookie": "must-not-pass"},
    )
    services.db.record_refresh_success(
        digest,
        1,
        "base64",
        {"subscription-userinfo": "upload=1"},
        100,
        source="fallback",
    )
    response = client.get(f"/raw/{token}")
    assert response.content == b"exact-upstream-bytes"
    assert response.headers["subscription-userinfo"] == "upload=1"
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"

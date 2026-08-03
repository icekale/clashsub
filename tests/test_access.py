import logging

from clashsub.access import AccessPolicy, SlidingWindowLimiter
from clashsub.events import redact


def test_untrusted_forwarded_header_cannot_turn_public_ip_private():
    policy = AccessPolicy(("172.18.0.0/16",))
    assert str(policy.effective_ip("8.8.8.8", "192.168.1.20")) == "8.8.8.8"
    assert policy.allowed("lan", "8.8.8.8", "192.168.1.20") is False
    assert str(policy.effective_ip("172.18.0.2", "not-an-ip")) == "172.18.0.2"


def test_trusted_proxy_chain_is_parsed_right_to_left():
    policy = AccessPolicy(("172.18.0.0/16", "10.0.0.0/8"))
    assert str(policy.effective_ip("172.18.0.2", "1.1.1.1, 10.0.0.4")) == "1.1.1.1"
    assert policy.allowed("public", "172.18.0.2", "1.1.1.1, 10.0.0.4") is True
    assert policy.is_https("172.18.0.2", "http", "https") is True
    assert policy.is_https("8.8.8.8", "http", "https") is False


def test_rate_limit_and_redaction_hide_bearers():
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
    assert limiter.allow("login:192.0.2.1", now=1)
    assert limiter.allow("login:192.0.2.1", now=2)
    assert not limiter.allow("login:192.0.2.1", now=3)
    text = redact("GET /raw/abcdefghijklmnopqrstuvwxyz012345 token=secret Authorization: Bearer abc")
    assert "abcdefghijklmnopqrstuvwxyz012345" not in text
    assert "secret" not in text and "Bearer abc" not in text


def test_redaction_covers_all_share_formats():
    for route in ("raw", "clash", "surge", "loon", "smart"):
        text = redact(f"GET /{route}/abcdefghijklmnopqrstuvwxyz012345 HTTP/1.1")
        assert "abcdefghijklmnopqrstuvwxyz012345" not in text

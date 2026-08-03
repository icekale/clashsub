from __future__ import annotations

from collections import defaultdict, deque
from ipaddress import ip_address, ip_network


LAN_NETWORKS = tuple(
    ip_network(value)
    for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "fc00::/7",
        "fe80::/10",
        "::1/128",
    )
)


class AccessPolicy:
    def __init__(self, trusted_proxy_cidrs):
        self.trusted = tuple(ip_network(value) for value in trusted_proxy_cidrs)

    def _trusted(self, address):
        return any(address in network for network in self.trusted)

    def effective_ip(self, peer: str, forwarded_for: str | None):
        current = ip_address(peer)
        if not forwarded_for or not self._trusted(current):
            return current
        try:
            chain = [ip_address(value.strip()) for value in forwarded_for.split(",") if value.strip()]
        except ValueError:
            return current
        for candidate in reversed(chain):
            if not self._trusted(current):
                break
            current = candidate
        return current

    def allowed(self, mode: str, peer: str, forwarded_for: str | None) -> bool:
        address = self.effective_ip(peer, forwarded_for)
        return mode == "public" or any(address in network for network in LAN_NETWORKS)

    def is_https(self, peer: str, direct_scheme: str, forwarded_proto: str | None) -> bool:
        if direct_scheme == "https":
            return True
        address = ip_address(peer)
        return self._trusted(address) and (forwarded_proto or "").split(",")[0].strip().lower() == "https"


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self.entries = defaultdict(deque)

    def allow(self, key: str, now: float) -> bool:
        values = self.entries[key]
        while values and values[0] <= now - self.window:
            values.popleft()
        if len(values) >= self.limit:
            return False
        values.append(now)
        return True

    def clear(self, key: str) -> None:
        self.entries.pop(key, None)

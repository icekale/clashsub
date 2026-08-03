from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import SecretStr

from .v2board_client import V2BoardClient


SourceName = Literal["protocol", "fallback"]


@dataclass(frozen=True)
class ResolvedSubscription:
    source: SourceName
    url: SecretStr = field(repr=False)
    expires_at: float | None = None
    login_succeeded_at: float | None = None
    resolved_at: float | None = None
    user_agent: str = "clash.meta"


class SubscriptionSource(Protocol):
    name: SourceName

    async def fetch(self) -> ResolvedSubscription:
        raise NotImplementedError


class V2BoardSubscriptionSource:
    name: Literal["protocol"] = "protocol"

    def __init__(
        self,
        client: V2BoardClient,
        credential_provider: Callable[[], tuple[str, str]] | None = None,
    ):
        self.client, self.credential_provider = client, credential_provider

    async def fetch(
        self,
        credentials: tuple[str, str] | None = None,
    ) -> ResolvedSubscription:
        selected = credentials
        if selected is None and self.credential_provider is not None:
            selected = self.credential_provider()
        result = (
            await self.client.fetch_subscription(selected)
            if selected is not None
            else await self.client.fetch_subscription()
        )
        return ResolvedSubscription(
            source=self.name,
            url=result.url,
            expires_at=result.expires_at,
            login_succeeded_at=result.login_succeeded_at,
            resolved_at=result.resolved_at,
            user_agent=result.user_agent,
        )


@dataclass(frozen=True)
class StaticUrlSource:
    url: SecretStr = field(repr=False)
    name: Literal["fallback"] = field(default="fallback", init=False)

    async def fetch(self) -> ResolvedSubscription:
        return ResolvedSubscription(self.name, self.url)

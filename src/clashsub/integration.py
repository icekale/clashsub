from __future__ import annotations

import time

from .events import get_logger
from .health import HealthSummary, NodeHealthChecker
from .openclash_client import OpenClashClient, OpenClashError
from .secret_store import SecretStore, SecretStoreUnavailable
from .settings import RuntimeSettings, SettingsStore


logger = get_logger("integration")

OPENCLASH_SECRET_NAME = "openclash_api_secret"


class IntegrationService:
    def __init__(
        self,
        settings_store: SettingsStore,
        credential_store: SecretStore,
        health_checker: NodeHealthChecker,
        client_factory=None,
        transport=None,
        refresher=None,
    ):
        self.settings_store = settings_store
        self.credential_store = credential_store
        self.health_checker = health_checker
        self.transport = transport
        self.refresher = refresher
        self._last_auto_refresh = 0.0
        self._auto_refresh_in_flight = False
        self.client_factory = client_factory or (
            lambda base_url, secret: OpenClashClient(base_url, secret, transport=self.transport)
        )

    def _client(self, settings: RuntimeSettings) -> OpenClashClient | None:
        if not settings.openclash_enabled:
            return None
        try:
            secret = self.credential_store.get(OPENCLASH_SECRET_NAME) or ""
        except SecretStoreUnavailable:
            secret = ""
        if not secret:
            logger.warning("openclash push skipped: api secret is not configured")
            return None
        return self.client_factory(settings.openclash_api_url, secret)

    async def run_health(self, settings: RuntimeSettings | None = None) -> HealthSummary:
        current = settings or self.settings_store.get()
        if not current.health_enabled:
            return HealthSummary(0, 0, None)
        summary = await self.health_checker.run_once(
            timeout_seconds=current.health_timeout_seconds
        )
        await self._maybe_auto_refresh(current, summary)
        return summary

    async def _maybe_auto_refresh(self, settings: RuntimeSettings, summary: HealthSummary) -> None:
        if not settings.health_refresh_enabled or self.refresher is None:
            return
        if not summary.total:
            return
        # 刷新成功后的 on_refreshed hook 会再次进入这里；用 in-flight 标志防止
        # 在 refresh() 完成前的重入触发无限循环。
        if self._auto_refresh_in_flight:
            return
        now = time.monotonic()
        if now - self._last_auto_refresh < settings.health_refresh_cooldown_minutes * 60:
            return
        ratio = summary.online / summary.total
        if ratio >= settings.health_refresh_online_ratio:
            return
        logger.warning(
            "node availability degraded total=%d online=%d ratio=%.2f, refreshing upstream cache",
            summary.total,
            summary.online,
            ratio,
        )
        self._auto_refresh_in_flight = True
        try:
            await self.refresher.refresh()
        except Exception:
            logger.exception("auto refresh after degraded health failed")
        finally:
            # 尝试结束后再记时间戳：失败的尝试不会白占冷却窗口，
            # 也能避免慢刷新进行期间又触发下一次自动刷新。
            self._auto_refresh_in_flight = False
            self._last_auto_refresh = time.monotonic()

    async def sync_after_refresh(self) -> None:
        settings = self.settings_store.get()
        try:
            if settings.health_enabled:
                await self.run_health(settings)
            client = self._client(settings)
            if client is None:
                return
            await client.refresh_provider(settings.openclash_provider.strip())
            logger.info(
                "openclash provider refreshed provider=%s",
                settings.openclash_provider.strip(),
            )
        except OpenClashError as exc:
            logger.warning("openclash push failed: %s", exc)
        except Exception:
            logger.exception("integration sync failed")

    async def push_now(self) -> dict:
        settings = self.settings_store.get()
        if not settings.openclash_enabled:
            raise OpenClashError("openclash integration is disabled")
        client = self._client(settings)
        if client is None:
            raise OpenClashError("openclash api secret is not configured")
        return await client.refresh_provider(settings.openclash_provider.strip())

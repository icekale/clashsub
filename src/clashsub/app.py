import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .access import AccessPolicy, SlidingWindowLimiter
from .api import admin, auth as auth_api, public
from .auth import AuthService
from .cache_files import CacheFiles
from .config import Settings
from .converter import ConverterService
from .db import Database
from .events import configure_logging
from .health import NodeHealthChecker
from .integration import IntegrationService
from .scheduler import RefreshScheduler
from .settings import RuntimeSettings, SettingsStore
from .secret_store import SecretStore, SecretStoreUnavailable
from .shares import ShareService
from .sources import StaticUrlSource, V2BoardSubscriptionSource
from .subscription import UpstreamRefresher
from .v2board_client import V2BoardClient


@dataclass
class Services:
    config: Settings
    db: Database
    cache: CacheFiles
    runtime_settings: SettingsStore
    shares: ShareService
    converter: ConverterService
    auth: AuthService
    access: AccessPolicy
    share_limiter: SlidingWindowLimiter
    login_limiter: SlidingWindowLimiter
    refresher: UpstreamRefresher
    credential_store: SecretStore
    integration: IntegrationService
    transport: object | None = None
    scheduler: object | None = None
    health_scheduler: object | None = None


def build_services(config: Settings, transport=None, resolver=None) -> Services:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(config.data_dir / "state.db")
    db.initialize()
    db.delete_expired_sessions(time.time())
    runtime = SettingsStore(db)
    if not runtime.get().lan_base_url:
        runtime.update(RuntimeSettings(lan_base_url="http://127.0.0.1:8080"))
    cache = CacheFiles(config.data_dir / "cache")
    credential_store = SecretStore(db, config.encryption_key_file)
    auth = AuthService(db)
    auth.bootstrap(
        config.initial_username.get_secret_value(),
        config.initial_password.get_secret_value(),
    )
    sources = []
    if config.protocol_configured:
        assert config.airport_api_base_url is not None
        assert config.airport_email is not None
        assert config.airport_password is not None
        if credential_store.available:
            try:
                credentials_exist = credential_store.get("airport_credentials") is not None
            except SecretStoreUnavailable:
                credentials_exist = True
            if not credentials_exist:
                credential_store.put(
                    "airport_credentials",
                    json.dumps(
                        {
                            "username": config.airport_email.get_secret_value(),
                            "password": config.airport_password.get_secret_value(),
                        },
                        separators=(",", ":"),
                    ),
                )

        def credential_provider() -> tuple[str, str]:
            try:
                raw = credential_store.get("airport_credentials")
                if raw:
                    record = json.loads(raw)
                    if isinstance(record, dict) and isinstance(record.get("username"), str) and isinstance(record.get("password"), str):
                        return record["username"], record["password"]
            except (SecretStoreUnavailable, ValueError, TypeError, json.JSONDecodeError):
                pass
            return config.airport_email.get_secret_value(), config.airport_password.get_secret_value()

        sources.append(
            V2BoardSubscriptionSource(
                V2BoardClient(
                    config.airport_api_base_url,
                    config.airport_email,
                    config.airport_password,
                    transport=transport,
                ),
                credential_provider=credential_provider,
            )
        )
    if config.upstream_url is not None:
        sources.append(StaticUrlSource(config.upstream_url))
    health_checker = NodeHealthChecker(db, cache)
    integration = IntegrationService(
        runtime,
        credential_store,
        health_checker,
        transport=transport,
    )
    refresher = UpstreamRefresher(
        db,
        cache,
        tuple(sources),
        transport=transport,
        max_bytes=config.max_response_bytes,
        resolver=resolver,
        allowed_download_cidrs=config.download_allowed_cidrs,
        credential_store=credential_store,
        on_refreshed=integration.sync_after_refresh,
    )
    integration.refresher = refresher
    return Services(
        config=config,
        db=db,
        cache=cache,
        runtime_settings=runtime,
        shares=ShareService(db, runtime, credential_store),
        converter=ConverterService(cache, config.converter_base_url, transport=transport),
        auth=auth,
        access=AccessPolicy(config.trusted_proxy_cidrs),
        share_limiter=SlidingWindowLimiter(120, 60),
        login_limiter=SlidingWindowLimiter(5, 900),
        refresher=refresher,
        credential_store=credential_store,
        integration=integration,
        transport=transport,
    )


class _HealthTask:
    def __init__(self, integration: IntegrationService):
        self.integration = integration

    async def refresh(self):
        await self.integration.run_health()


def create_app(
    config: Settings,
    transport=None,
    start_scheduler: bool = False,
    resolver=None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services = build_services(config, transport, resolver)
        configure_logging(config.data_dir / "logs" / "events.log")
        scheduler = RefreshScheduler(
            services.refresher,
            delay_seconds=lambda: 24 * 3600,
        )
        health_scheduler = RefreshScheduler(
            _HealthTask(services.integration),
            delay_seconds=lambda: services.runtime_settings.get().health_interval_minutes * 60,
        )
        services.scheduler = scheduler
        services.health_scheduler = health_scheduler
        app.state.services = services
        tasks = []
        if app.state.start_scheduler:
            tasks.append(asyncio.create_task(scheduler.run(), name="subscription-refresh"))
            tasks.append(asyncio.create_task(health_scheduler.run(), name="node-health"))
        try:
            yield
        finally:
            await scheduler.stop()
            await health_scheduler.stop()
            for task in tasks:
                await task

    app = FastAPI(title="Clash Subscription Cache", lifespan=lifespan)
    app.state.start_scheduler = start_scheduler

    @app.middleware("http")
    async def enforce_access_mode(request: Request, call_next):
        path = request.url.path
        protected = any(
            path == prefix or path.startswith(f"{prefix}/")
            for prefix in ("/app", "/api", "/raw", "/clash", "/clash-ha", "/surge", "/loon", "/smart")
        )
        if protected:
            services = request.app.state.services
            peer = request.client.host if request.client else ""
            try:
                allowed = services.access.allowed(
                    services.runtime_settings.get().access_mode,
                    peer,
                    request.headers.get("x-forwarded-for"),
                )
            except ValueError:
                allowed = False
            if not allowed:
                return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await call_next(request)

    app.include_router(public.router)
    app.include_router(auth_api.router)
    app.include_router(admin.router)

    app.mount(
        "/app/assets",
        StaticFiles(directory=config.frontend_dir / "assets", check_dir=False),
        name="app-assets",
    )

    @app.get("/", include_in_schema=False)
    def root_redirect():
        return RedirectResponse("/app/")

    @app.get("/app/", include_in_schema=False)
    @app.get("/app/{path:path}", include_in_schema=False)
    def spa(path: str = ""):
        index = config.frontend_dir / "index.html"
        if not index.is_file():
            raise HTTPException(404, "frontend not built")
        return FileResponse(index)

    return app


def production_app() -> FastAPI:
    return create_app(Settings.from_env(), start_scheduler=True)

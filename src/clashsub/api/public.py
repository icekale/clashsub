import time

import yaml

from fastapi import APIRouter, HTTPException, Request, Response

from ..events import get_logger


router = APIRouter()
logger = get_logger("public")
_SAFE_HEADERS = {"subscription-userinfo", "profile-update-interval"}


def _services(request: Request):
    return request.app.state.services


def _allow_request(request: Request, scope: str):
    services = _services(request)
    peer = request.client.host
    forwarded = request.headers.get("x-forwarded-for")
    mode = services.runtime_settings.get().access_mode
    if not services.access.allowed(mode, peer, forwarded):
        raise HTTPException(404)
    effective = str(services.access.effective_ip(peer, forwarded))
    if scope == "share" and not services.share_limiter.allow(f"share:{effective}", time.time()):
        raise HTTPException(429, "too many requests")


async def _raw_response(request: Request, token: str, require_clash: bool = False):
    services = _services(request)
    share = services.shares.resolve(token, require_clash=require_clash)
    if not share:
        raise HTTPException(404)
    try:
        await services.refresher.refresh_if_stale(
            services.runtime_settings.get().refresh_interval_minutes * 60
        )
    except Exception as exc:
        logger.warning("on-demand refresh failed: %s", type(exc).__name__)
    state = services.db.runtime_state()
    if not state["current_digest"]:
        raise HTTPException(503, "subscription cache unavailable")
    snapshot = services.cache.read_raw(state["current_digest"])
    headers = {
        key: value
        for key, value in snapshot.safe_headers.items()
        if key in _SAFE_HEADERS
    }
    headers["Cache-Control"] = "no-store"
    return Response(snapshot.payload, media_type="text/plain; charset=utf-8", headers=headers)


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/raw/{token}")
async def raw_subscription(token: str, request: Request):
    _allow_request(request, "share")
    return await _raw_response(request, token)


@router.get("/clash-ha/{token}")
async def ha_subscription(token: str, request: Request):
    _allow_request(request, "share")
    services = _services(request)
    share = services.shares.resolve(token, require_clash=True)
    if not share:
        raise HTTPException(404)
    settings = services.runtime_settings.get()
    try:
        await services.refresher.refresh_if_stale(settings.refresh_interval_minutes * 60)
    except Exception as exc:
        logger.warning("on-demand refresh failed: %s", type(exc).__name__)
    state = services.db.runtime_state()
    digest = state["current_digest"] if state else None
    if not digest:
        raise HTTPException(503, "subscription cache unavailable")
    try:
        snapshot = services.cache.read_raw(digest)
        document = yaml.safe_load(snapshot.payload)
    except (OSError, yaml.YAMLError, AttributeError):
        raise HTTPException(503, "subscription cache unavailable") from None
    proxies = document.get("proxies") if isinstance(document, dict) else None
    if not isinstance(proxies, list):
        raise HTTPException(503, "subscription cache unavailable")

    recent_unhealthy: set[str] = set()
    if settings.health_enabled:
        rows = services.db.list_node_health()
        now = time.time()
        freshness_window = max(2 * settings.health_interval_minutes * 60, 600)
        recent_unhealthy = {
            row["name"]
            for row in rows
            if not row["ok"] and now - row["checked_at"] <= freshness_window
        }
    filtered = [proxy for proxy in proxies if proxy.get("name") not in recent_unhealthy]
    document["proxies"] = filtered
    body = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    headers = {"Cache-Control": "no-store"}
    try:
        safe_headers = {
            key: value
            for key, value in snapshot.safe_headers.items()
            if key in _SAFE_HEADERS
        }
    except AttributeError:
        safe_headers = {}
    headers.update(safe_headers)
    return Response(body, media_type="text/plain; charset=utf-8", headers=headers)


async def _converted_subscription(token: str, request: Request, format: str):
    _allow_request(request, "share")
    services = _services(request)
    settings = services.runtime_settings.get()
    share = services.shares.resolve(token, require_clash=True)
    if not settings.converter_enabled or not share:
        raise HTTPException(404)
    try:
        await services.refresher.refresh_if_stale(settings.refresh_interval_minutes * 60)
    except Exception as exc:
        logger.warning("on-demand refresh failed: %s", type(exc).__name__)
    source_base = services.config.converter_source_base_url or settings.active_base_url()
    raw_url = f"{source_base.rstrip('/')}/raw/{token}"
    public_raw_url = f"{settings.active_base_url().rstrip('/')}/raw/{token}"
    if not raw_url.startswith(("http://", "https://")) or not public_raw_url.startswith(
        ("http://", "https://")
    ):
        raise HTTPException(404)
    state = services.db.runtime_state()
    source_digest = state["current_digest"] if state else None
    try:
        body = await services.converter.render(
            share["id"],
            raw_url,
            format,
            public_raw_url=public_raw_url,
            source_digest=source_digest,
        )
    except RuntimeError as exc:
        logger.warning("converter unavailable format=%s", format)
        raise HTTPException(503, "converter unavailable") from exc
    media_type = "text/yaml; charset=utf-8" if format == "clash" else "text/plain; charset=utf-8"
    headers = {"Cache-Control": "no-store"}
    if state["current_digest"]:
        try:
            snapshot = services.cache.read_raw(source_digest)
        except OSError:
            snapshot = None
        if snapshot is not None:
            headers.update(
                {key: value for key, value in snapshot.safe_headers.items() if key in _SAFE_HEADERS}
            )
    return Response(body, media_type=media_type, headers=headers)


@router.get("/clash/{token}")
async def clash_subscription(token: str, request: Request):
    return await _converted_subscription(token, request, "clash")


@router.get("/surge/{token}")
async def surge_subscription(token: str, request: Request):
    return await _converted_subscription(token, request, "surge")


@router.get("/loon/{token}")
async def loon_subscription(token: str, request: Request):
    return await _converted_subscription(token, request, "loon")


@router.get("/smart/{token}")
async def smart_subscription(token: str, request: Request):
    user_agent = request.headers.get("user-agent", "").lower()
    if "surge" in user_agent:
        return await _converted_subscription(token, request, "surge")
    if "loon" in user_agent:
        return await _converted_subscription(token, request, "loon")
    if any(
        marker in user_agent
        for marker in ("clash", "mihomo", "openclash", "stash", "karing", "shadowrocket")
    ):
        return await _converted_subscription(token, request, "clash")
    _allow_request(request, "share")
    return await _raw_response(request, token, require_clash=True)

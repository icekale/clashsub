import asyncio
import time
from ipaddress import ip_address

import yaml

from fastapi import APIRouter, HTTPException, Request, Response

from ..converter import client_params
from ..events import get_logger


router = APIRouter()
logger = get_logger("public")
_SAFE_HEADERS = {"subscription-userinfo", "profile-update-interval"}
_MEDIA_TYPES = {
    "clash": "text/yaml; charset=utf-8",
    "singbox": "application/json; charset=utf-8",
}


def _services(request: Request):
    return request.app.state.services


def _allow_request(request: Request, scope: str):
    services = _services(request)
    if request.client is None:
        raise HTTPException(400, "client address unavailable")
    peer = request.client.host
    forwarded = request.headers.get("x-forwarded-for")
    mode = services.runtime_settings.get().access_mode
    if not services.access.allowed(mode, peer, forwarded):
        raise HTTPException(404)
    effective = str(services.access.effective_ip(peer, forwarded))
    if (
        scope == "share"
        and not _is_loopback(effective)
        and not services.share_limiter.allow(f"share:{effective}", time.time())
    ):
        raise HTTPException(429, "too many requests")


def _rewrite_proxy_groups(document: dict, proxies: list) -> None:
    groups = document.get("proxy-groups")
    if not isinstance(groups, list):
        return
    keep = {str(proxy.get("name", "")).strip() for proxy in proxies if isinstance(proxy, dict)}
    keep.update(
        str(group.get("name", "")).strip()
        for group in groups
        if isinstance(group, dict)
    )
    keep.update({"DIRECT", "REJECT", "PASS", "COMPATIBLE"})
    keep.discard("")
    for group in groups:
        members = group.get("proxies") if isinstance(group, dict) else None
        if isinstance(members, list):
            group["proxies"] = [name for name in members if str(name).strip() in keep]


def _is_loopback(address: str) -> bool:
    try:
        return ip_address(address).is_loopback
    except ValueError:
        return False


def _profile_update_interval_hours(refresh_interval_minutes: int) -> str:
    return str(max(1, (refresh_interval_minutes + 59) // 60))


def _cache_headers(snapshot, refresh_interval_minutes: int) -> dict[str, str]:
    try:
        headers = {
            key: value
            for key, value in snapshot.safe_headers.items()
            if key in _SAFE_HEADERS
        }
    except AttributeError:
        headers = {}
    # 机场常下发 24 小时；按 ClashSub 自己的刷新间隔覆盖，否则 OpenClash 会按
    # profile-update-interval 把订阅当成一天一更。
    headers["profile-update-interval"] = _profile_update_interval_hours(
        refresh_interval_minutes
    )
    return headers


async def _refresh_state(services):
    try:
        await services.refresher.refresh_if_stale(
            services.runtime_settings.get().refresh_interval_minutes * 60
        )
    except Exception as exc:
        logger.warning("on-demand refresh failed: %s", type(exc).__name__)
    return services.db.runtime_state()


def _served_snapshot(services):
    try:
        return services.backup_nodes.served_snapshot()
    except (FileNotFoundError, OSError):
        logger.warning("raw cache unreadable")
        raise HTTPException(503, "subscription cache unavailable") from None


async def _raw_response(request: Request, token: str, require_clash: bool = False):
    services = _services(request)
    share = services.shares.resolve(token, require_clash=require_clash)
    if not share:
        raise HTTPException(404)
    await _refresh_state(services)
    snapshot = _served_snapshot(services)
    headers = {
        **_cache_headers(snapshot, services.runtime_settings.get().refresh_interval_minutes),
        "Cache-Control": "no-store",
    }
    return Response(snapshot.payload, media_type="text/plain; charset=utf-8", headers=headers)


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/internal/raw")
async def internal_raw(request: Request):
    """同容器转换进程按 URL 拉取原始订阅用的内部通道：不需要分享 token。

    只认真实对端地址（不看 X-Forwarded-For），反代转发的请求一律 404，
    所以外网无法借伪造头拿到这条免鉴权路径。
    """
    if request.client is None or not _is_loopback(request.client.host):
        raise HTTPException(404)
    services = _services(request)
    await _refresh_state(services)
    return Response(
        _served_snapshot(services).payload,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


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
    await _refresh_state(services)
    if services.backup_nodes.is_active():
        return await clash_subscription(token, request)
    snapshot = _served_snapshot(services)
    try:
        document = await asyncio.to_thread(yaml.safe_load, snapshot.payload)
    except (yaml.YAMLError, RecursionError, AttributeError):
        raise HTTPException(503, "subscription cache unavailable") from None
    proxies = document.get("proxies") if isinstance(document, dict) else None
    if not isinstance(proxies, list):
        raise HTTPException(503, "subscription cache unavailable")

    recent_unhealthy: set[str] = set()
    if settings.health_enabled:
        rows = services.db.list_node_health()
        now = time.time()
        freshness_window = max(
            2 * settings.effective_health_interval(time.localtime().tm_hour),
            120,
        )
        recent_unhealthy = {
            row["name"]
            for row in rows
            if not row["ok"] and now - row["checked_at"] <= freshness_window
        }
    filtered = [
        proxy
        for proxy in proxies
        if str(proxy.get("name", "")).strip() not in recent_unhealthy
    ]
    document["proxies"] = filtered
    _rewrite_proxy_groups(document, filtered)
    body = await asyncio.to_thread(yaml.safe_dump, document, allow_unicode=True, sort_keys=False)
    headers = {
        **_cache_headers(snapshot, settings.refresh_interval_minutes),
        "Cache-Control": "no-store",
    }
    return Response(body, media_type="text/plain; charset=utf-8", headers=headers)


async def _converted_subscription(token: str, request: Request, format: str):
    _allow_request(request, "share")
    services = _services(request)
    settings = services.runtime_settings.get()
    share = services.shares.resolve(token, require_clash=True)
    if not settings.converter_enabled or not share:
        raise HTTPException(404)
    state = await _refresh_state(services)
    source_base = services.config.converter_source_base_url or settings.active_base_url()
    raw_url = f"{source_base.rstrip('/')}/raw/{token}"
    public_raw_url = f"{settings.active_base_url().rstrip('/')}/raw/{token}"
    if not raw_url.startswith(("http://", "https://")) or not public_raw_url.startswith(
        ("http://", "https://")
    ):
        raise HTTPException(404)
    headers = {"Cache-Control": "no-store"}
    if services.backup_nodes.is_active():
        headers["profile-update-interval"] = _profile_update_interval_hours(
            settings.refresh_interval_minutes
        )
        source_digest = None
    else:
        source_digest = state["current_digest"] if state else None
        if source_digest:
            try:
                headers.update(
                    _cache_headers(
                        services.cache.read_raw(source_digest),
                        settings.refresh_interval_minutes,
                    )
                )
            except OSError:
                pass
    try:
        body = await services.converter.render(
            share["id"],
            raw_url,
            format,
            public_raw_url=public_raw_url,
            source_digest=source_digest,
            params=client_params(request.query_params),
        )
    except RuntimeError as exc:
        logger.warning("converter unavailable format=%s", format)
        raise HTTPException(503, "converter unavailable") from exc
    return Response(
        body,
        media_type=_MEDIA_TYPES.get(format, "text/plain; charset=utf-8"),
        headers=headers,
    )


@router.get("/clash/{token}")
async def clash_subscription(token: str, request: Request):
    return await _converted_subscription(token, request, "clash")


@router.get("/surge/{token}")
async def surge_subscription(token: str, request: Request):
    return await _converted_subscription(token, request, "surge")


@router.get("/loon/{token}")
async def loon_subscription(token: str, request: Request):
    return await _converted_subscription(token, request, "loon")


@router.get("/quanx/{token}")
async def quanx_subscription(token: str, request: Request):
    return await _converted_subscription(token, request, "quanx")


@router.get("/surfboard/{token}")
async def surfboard_subscription(token: str, request: Request):
    return await _converted_subscription(token, request, "surfboard")


@router.get("/singbox/{token}")
async def singbox_subscription(token: str, request: Request):
    return await _converted_subscription(token, request, "singbox")


@router.get("/smart/{token}")
async def smart_subscription(token: str, request: Request):
    user_agent = request.headers.get("user-agent", "").lower()
    if "surge" in user_agent:
        return await _converted_subscription(token, request, "surge")
    if "loon" in user_agent:
        return await _converted_subscription(token, request, "loon")
    if "quantumult" in user_agent:
        return await _converted_subscription(token, request, "quanx")
    if "surfboard" in user_agent:
        return await _converted_subscription(token, request, "surfboard")
    if "sing-box" in user_agent or "singbox" in user_agent:
        return await _converted_subscription(token, request, "singbox")
    if any(
        marker in user_agent
        for marker in ("clash", "mihomo", "openclash", "stash", "karing")
    ):
        return await _converted_subscription(token, request, "clash")
    _allow_request(request, "share")
    return await _raw_response(request, token, require_clash=True)

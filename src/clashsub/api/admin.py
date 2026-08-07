from __future__ import annotations

import time
import json
from dataclasses import asdict
from typing import Literal, NoReturn

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..events import read_recent_events
from ..events import get_logger
from ..integration import OPENCLASH_SECRET_NAME
from ..openclash_client import OpenClashClient, OpenClashError
from ..secret_store import SecretStoreUnavailable
from ..settings import RuntimeSettings, validate_http_origin
from .auth import COOKIE_NAME, _delete_session_cookie


router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = get_logger("admin")


class CreateShareRequest(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    days: int = Field(default=365, ge=1, le=3650)
    allow_raw: bool = True
    allow_clash: bool = False


class RenewShareRequest(BaseModel):
    days: int = Field(ge=1, le=3650)


class RuntimeSettingsRequest(BaseModel):
    refresh_interval_minutes: int = Field(ge=1, le=1440)
    access_mode: Literal["lan", "public"]
    lan_base_url: str = Field(default="", max_length=2048)
    public_base_url: str = Field(default="", max_length=2048)
    converter_enabled: bool = False
    openclash_enabled: bool = False
    openclash_api_url: str = Field(default="", max_length=2048)
    openclash_provider: str = Field(default="", max_length=256)
    health_enabled: bool = False
    health_interval_seconds: int = Field(default=600, ge=30, le=86400)
    health_timeout_seconds: int = Field(default=5, ge=1, le=30)
    health_refresh_enabled: bool = False
    health_refresh_online_ratio: float = Field(default=0.5, ge=0.1, le=1.0)
    health_refresh_cooldown_minutes: int = Field(default=10, ge=1, le=1440)
    health_night_enabled: bool = False
    health_night_interval_seconds: int = Field(default=600, ge=30, le=86400)
    health_night_start_hour: int = Field(default=0, ge=0, le=23)
    health_night_end_hour: int = Field(default=8, ge=0, le=23)
    public_acknowledged: bool = False


class OpenClashCredentialsRequest(BaseModel):
    secret: str = Field(min_length=1, max_length=256)


class OpenClashTestRequest(BaseModel):
    api_url: str = Field(min_length=1, max_length=2048)
    secret: str = Field(min_length=1, max_length=256)


class UpstreamCredentialsRequest(BaseModel):
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(default="", max_length=1024)


class RevealShareRequest(BaseModel):
    kind: Literal["raw", "clash", "surge", "loon", "smart"]


def _services(request: Request):
    return request.app.state.services


def require_admin(request: Request, require_csrf: bool = False):
    services = _services(request)
    session_token = request.cookies.get(COOKIE_NAME, "")
    now = time.time()
    session = services.auth.authenticate(session_token, None, now)
    if not session:
        raise HTTPException(401, "unauthorized")
    if require_csrf:
        csrf = request.headers.get("x-csrf-token")
        if not services.auth.authenticate(session_token, csrf, now, require_csrf=True):
            raise HTTPException(403, "invalid CSRF token")
    return session


def _missing_share(exc: KeyError) -> NoReturn:
    raise HTTPException(404, "share not found") from exc


@router.get("/overview")
def overview(request: Request):
    require_admin(request)
    services = _services(request)
    state = services.db.runtime_state()
    converter_enabled = services.runtime_settings.get().converter_enabled
    return {
        "has_cache": state["current_digest"] is not None,
        "stale": state["consecutive_failures"] > 0,
        "node_count": state["node_count"],
        "content_format": state["content_format"],
        "last_attempt_at": state["last_attempt_at"],
        "last_success_at": state["last_success_at"],
        "last_success_source": state["last_success_source"],
        "protocol_last_login_at": state["protocol_last_login_at"],
        "protocol_last_subscribe_at": state["protocol_last_subscribe_at"],
        "protocol_subscription_expires_at": state["protocol_subscription_expires_at"],
        "protocol_last_error_category": state["protocol_last_error_category"],
        "consecutive_failures": state["consecutive_failures"],
        "last_error": state["last_error"],
        "converter_enabled": converter_enabled,
        "converter_status": "enabled" if converter_enabled else "disabled",
    }


@router.get("/shares")
def list_shares(request: Request):
    require_admin(request)
    now = time.time()
    return [
        {**asdict(item), "expired": item.expires_at <= now}
        for item in _services(request).shares.list()
    ]


@router.post("/shares", status_code=201)
def create_share(payload: CreateShareRequest, request: Request):
    require_admin(request, require_csrf=True)
    try:
        created = _services(request).shares.create(
            payload.label,
            payload.days,
            payload.allow_raw,
            payload.allow_clash,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    logger.info("share created id=%s label=%s", created.id, payload.label.strip())
    return asdict(created)


@router.post("/shares/{share_id}/renew", status_code=204)
def renew_share(share_id: str, payload: RenewShareRequest, request: Request):
    require_admin(request, require_csrf=True)
    try:
        _services(request).shares.renew(share_id, payload.days)
    except KeyError as exc:
        _missing_share(exc)
    logger.info("share renewed id=%s days=%d", share_id, payload.days)
    return Response(status_code=204)


@router.post("/shares/{share_id}/revoke", status_code=204)
def revoke_share(share_id: str, request: Request):
    require_admin(request, require_csrf=True)
    try:
        _services(request).shares.revoke(share_id)
    except KeyError as exc:
        _missing_share(exc)
    logger.info("share revoked id=%s", share_id)
    return Response(status_code=204)


@router.post("/shares/{share_id}/rotate")
def rotate_share(share_id: str, request: Request):
    require_admin(request, require_csrf=True)
    try:
        created = _services(request).shares.rotate(share_id)
    except KeyError as exc:
        _missing_share(exc)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    logger.info("share rotated id=%s", share_id)
    return asdict(created)


@router.post("/shares/{share_id}/reveal")
def reveal_share(share_id: str, payload: RevealShareRequest, request: Request):
    require_admin(request, require_csrf=True)
    try:
        url = _services(request).shares.reveal(share_id, payload.kind)
    except KeyError as exc:
        raise HTTPException(404, "share not found") from exc
    except RuntimeError as exc:
        raise HTTPException(503, "share recovery unavailable") from exc
    return JSONResponse({"url": url}, headers={"Cache-Control": "no-store"})


@router.delete("/shares/{share_id}", status_code=204)
def delete_share(share_id: str, request: Request):
    require_admin(request, require_csrf=True)
    services = _services(request)
    try:
        services.shares.delete(share_id)
    except KeyError as exc:
        _missing_share(exc)
    services.cache.remove_converted(share_id)
    logger.info("share deleted id=%s", share_id)
    return Response(status_code=204)


@router.get("/settings")
def get_settings(request: Request):
    require_admin(request)
    return asdict(_services(request).runtime_settings.get())


@router.put("/settings")
def update_settings(payload: RuntimeSettingsRequest, request: Request):
    require_admin(request, require_csrf=True)
    services = _services(request)
    current = services.runtime_settings.get()
    if current.access_mode != "public" and payload.access_mode == "public" and not payload.public_acknowledged:
        raise HTTPException(400, "public mode acknowledgement is required")
    updated = RuntimeSettings(
        refresh_interval_minutes=payload.refresh_interval_minutes,
        access_mode=payload.access_mode,
        lan_base_url=payload.lan_base_url,
        public_base_url=payload.public_base_url,
        converter_enabled=payload.converter_enabled,
        openclash_enabled=payload.openclash_enabled,
        openclash_api_url=payload.openclash_api_url,
        openclash_provider=payload.openclash_provider,
        health_enabled=payload.health_enabled,
        health_interval_seconds=payload.health_interval_seconds,
        health_timeout_seconds=payload.health_timeout_seconds,
        health_refresh_enabled=payload.health_refresh_enabled,
        health_refresh_online_ratio=payload.health_refresh_online_ratio,
        health_refresh_cooldown_minutes=payload.health_refresh_cooldown_minutes,
        health_night_enabled=payload.health_night_enabled,
        health_night_interval_seconds=payload.health_night_interval_seconds,
        health_night_start_hour=payload.health_night_start_hour,
        health_night_end_hour=payload.health_night_end_hour,
    )
    try:
        services.runtime_settings.update(updated)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if current.refresh_interval_minutes != updated.refresh_interval_minutes and services.scheduler is not None:
        services.scheduler.reschedule()
    if getattr(services, "health_scheduler", None) is not None and (
        current.health_interval_seconds != updated.health_interval_seconds
        or current.health_night_enabled != updated.health_night_enabled
        or current.health_night_interval_seconds != updated.health_night_interval_seconds
        or current.health_night_start_hour != updated.health_night_start_hour
        or current.health_night_end_hour != updated.health_night_end_hour
    ):
        services.health_scheduler.reschedule()
    reauthenticate = current.access_mode != updated.access_mode
    if reauthenticate:
        services.db.delete_all_sessions()
    response = JSONResponse({**asdict(updated), "reauthenticate": reauthenticate})
    if reauthenticate:
        _delete_session_cookie(response, request)
    return response


@router.post("/upstream/refresh")
async def refresh_upstream(request: Request):
    require_admin(request, require_csrf=True)
    return asdict(await _services(request).refresher.refresh())


@router.get("/upstream/credentials")
def get_upstream_credentials(request: Request):
    require_admin(request)
    services = _services(request)
    username = services.config.airport_email.get_secret_value() if services.config.airport_email else ""
    password_configured = services.config.airport_password is not None
    try:
        raw = services.credential_store.get("airport_credentials")
        if raw:
            record = json.loads(raw)
            if isinstance(record, dict):
                username = record.get("username", username)
                password_configured = bool(record.get("password"))
    except (SecretStoreUnavailable, ValueError, TypeError, json.JSONDecodeError):
        pass
    return {
        "username": username,
        "password_configured": password_configured,
        "management_available": services.credential_store.available,
    }


@router.put("/upstream/credentials")
async def update_upstream_credentials(payload: UpstreamCredentialsRequest, request: Request):
    require_admin(request, require_csrf=True)
    services = _services(request)
    username = payload.username.strip()
    if not username:
        raise HTTPException(400, "username is required")
    password = payload.password
    if not password:
        try:
            raw = services.credential_store.get("airport_credentials")
            if raw:
                record = json.loads(raw)
                password = record.get("password", "") if isinstance(record, dict) else ""
        except (SecretStoreUnavailable, ValueError, TypeError, json.JSONDecodeError):
            password = ""
        if not password and services.config.airport_password:
            password = services.config.airport_password.get_secret_value()
    if not password:
        raise HTTPException(400, "password is required")
    result = await services.refresher.update_protocol_credentials(username, password)
    if not result.ok:
        status = 503 if result.error_category == "secret_store_unavailable" else 400
        raise HTTPException(status, result.error_category or "credential validation failed")
    return asdict(result)


@router.get("/upstream/status")
def upstream_status(request: Request):
    require_admin(request)
    config = _services(request).config
    return {
        "protocol_configured": config.protocol_configured,
        "api_base_url": config.airport_api_base_url,
        "email_configured": config.airport_email is not None,
        "password_configured": config.airport_password is not None,
        "fallback_configured": config.upstream_url is not None,
    }


@router.post("/upstream/test")
async def test_upstream(request: Request):
    require_admin(request, require_csrf=True)
    return asdict(await _services(request).refresher.test_protocol())


@router.get("/logs")
def logs(request: Request, limit: int = Query(default=200, ge=1, le=500)):
    require_admin(request)
    path = _services(request).config.data_dir / "logs" / "events.log"
    return {"lines": read_recent_events(path, limit)}


@router.get("/openclash/credentials")
def get_openclash_credentials(request: Request):
    require_admin(request)
    services = _services(request)
    try:
        configured = services.credential_store.get(OPENCLASH_SECRET_NAME) is not None
    except SecretStoreUnavailable:
        configured = False
    return {"configured": configured}


@router.put("/openclash/credentials")
def update_openclash_credentials(payload: OpenClashCredentialsRequest, request: Request):
    require_admin(request, require_csrf=True)
    services = _services(request)
    try:
        services.credential_store.put(OPENCLASH_SECRET_NAME, payload.secret)
    except SecretStoreUnavailable as exc:
        raise HTTPException(503, "secret store unavailable") from exc
    logger.info("openclash api secret updated")
    return {"configured": True}


@router.post("/openclash/test")
async def test_openclash(payload: OpenClashTestRequest, request: Request):
    require_admin(request, require_csrf=True)
    services = _services(request)
    try:
        api_url = validate_http_origin(payload.api_url, "OpenClash API URL")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    client = OpenClashClient(api_url, payload.secret, transport=services.transport)
    try:
        info = await client.version()
    except OpenClashError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "version": info.get("version", ""), "meta": bool(info.get("meta"))}


@router.post("/openclash/refresh")
async def refresh_openclash(request: Request):
    require_admin(request, require_csrf=True)
    services = _services(request)
    try:
        result = await services.integration.push_now()
    except OpenClashError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": True, **result}


@router.get("/health")
def health_overview(request: Request):
    require_admin(request)
    services = _services(request)
    settings = services.runtime_settings.get()
    rows = services.db.list_node_health()
    nodes = [
        {
            "name": row["name"],
            "ok": bool(row["ok"]),
            "latency_ms": row["latency_ms"],
            "checked_at": row["checked_at"],
        }
        for row in rows
    ]
    return {
        "enabled": settings.health_enabled,
        "interval_seconds": settings.health_interval_seconds,
        "night_enabled": settings.health_night_enabled,
        "night_interval_seconds": settings.health_night_interval_seconds,
        "night_start_hour": settings.health_night_start_hour,
        "night_end_hour": settings.health_night_end_hour,
        "timeout_seconds": settings.health_timeout_seconds,
        "checked_at": max((row["checked_at"] for row in rows), default=None),
        "total": len(nodes),
        "online": sum(1 for node in nodes if node["ok"]),
        "nodes": nodes,
    }


@router.post("/health/check")
async def run_health_check(request: Request):
    require_admin(request, require_csrf=True)
    services = _services(request)
    summary = await services.integration.run_health()
    return {
        "total": summary.total,
        "online": summary.online,
        "checked_at": summary.checked_at,
    }

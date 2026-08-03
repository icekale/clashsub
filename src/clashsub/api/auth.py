from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/auth", tags=["auth"])
COOKIE_NAME = "clashsub_session"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class CredentialRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_username: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=1024)


def _services(request: Request):
    return request.app.state.services


def _peer(request: Request) -> str:
    if request.client is None:
        raise HTTPException(400, "client address unavailable")
    return request.client.host


def _secure_cookie(request: Request) -> bool:
    services = _services(request)
    return services.access.is_https(
        _peer(request),
        request.url.scheme,
        request.headers.get("x-forwarded-proto"),
    )


def _set_session_cookie(response: Response, value: str, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME,
        value,
        max_age=24 * 3600,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


@router.post("/login")
def login(payload: LoginRequest, request: Request):
    services = _services(request)
    peer = _peer(request)
    forwarded = request.headers.get("x-forwarded-for")
    effective = str(services.access.effective_ip(peer, forwarded))
    limiter_key = f"login:{effective}"
    now = time.time()
    if not services.login_limiter.allow(limiter_key, now):
        raise HTTPException(429, "too many login attempts")
    try:
        result = services.auth.login(payload.username, payload.password, now)
    except PermissionError as exc:
        raise HTTPException(401, "invalid credentials") from exc
    services.login_limiter.clear(limiter_key)
    admin = services.db.get_admin()
    response = JSONResponse(
        {
            "username": admin["username"],
            "csrf_token": result.csrf_token,
            "expires_at": result.expires_at,
        }
    )
    _set_session_cookie(response, result.session_token, _secure_cookie(request))
    return response


@router.get("/session")
def restore_session(request: Request):
    services = _services(request)
    session_token = request.cookies.get(COOKIE_NAME, "")
    now = time.time()
    if not services.auth.authenticate(session_token, None, now):
        raise HTTPException(401, "unauthorized")
    csrf = services.auth.rotate_csrf(session_token, now)
    return {"username": services.db.get_admin()["username"], "csrf_token": csrf}


@router.post("/logout", status_code=204)
def logout(request: Request):
    services = _services(request)
    session_token = request.cookies.get(COOKIE_NAME, "")
    csrf = request.headers.get("x-csrf-token")
    if not services.auth.authenticate(session_token, csrf, time.time(), require_csrf=True):
        raise HTTPException(403, "invalid session or CSRF token")
    services.auth.logout(session_token)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/", samesite="strict")
    return response


@router.put("/credentials")
def change_credentials(payload: CredentialRequest, request: Request):
    services = _services(request)
    session_token = request.cookies.get(COOKIE_NAME, "")
    csrf = request.headers.get("x-csrf-token", "")
    try:
        services.auth.change_credentials(
            session_token,
            csrf,
            payload.current_password,
            payload.new_username,
            payload.new_password,
            time.time(),
        )
    except PermissionError as exc:
        raise HTTPException(403, "invalid credentials or session") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response = JSONResponse({"reauthenticate": True})
    response.delete_cookie(COOKIE_NAME, path="/", samesite="strict")
    return response

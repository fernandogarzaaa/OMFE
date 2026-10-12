"""/api/v1/auth — login, refresh, logout, me, TOTP."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Tenant, User
from ...security import tokens
from ...services import auth as auth_svc
from ..cookies import clear_session_cookies, set_session_cookies
from ..deps import check_csrf, client_ip, get_current_user
from ..errors import ApiError, error_body
from ..serializers import user_out

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    totp: str | None = Field(default=None, max_length=12)


class TotpIn(BaseModel):
    code: str = Field(min_length=6, max_length=12)


def _status_for(code: str) -> int:
    return 409 if code == "totp_already_enabled" else 400 if code == "totp_not_setup" else 401


def me_payload(db: Session, user: User) -> dict[str, Any]:
    return user_out(user, db.get(Tenant, user.tenant_id))


@router.post("/login")
def login(
    body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        sess = auth_svc.login(
            db,
            body.email,
            body.password,
            body.totp,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except auth_svc.AuthError as exc:
        db.commit()  # persist failed-attempt counters / lockout / audit before erroring
        raise ApiError(401, exc.code, exc.message) from exc
    set_session_cookies(response, sess.access_token, sess.refresh_token)
    return {"user": me_payload(db, sess.user)}


@router.post("/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> Any:
    check_csrf(request)
    try:
        sess = auth_svc.refresh(
            db,
            request.cookies.get(tokens.REFRESH_COOKIE),
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except auth_svc.AuthError as exc:
        db.commit()  # family revocation on reuse must survive the error response
        err = JSONResponse(error_body(exc.code, exc.message), status_code=401)
        clear_session_cookies(err)
        return err
    set_session_cookies(response, sess.access_token, sess.refresh_token)
    return {"user": me_payload(db, sess.user)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    check_csrf(request)
    auth_svc.logout(db, request.cookies.get(tokens.REFRESH_COOKIE))
    clear_session_cookies(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return me_payload(db, user)


@router.post("/totp/setup")
def totp_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        uri = auth_svc.totp_setup(db, user)
    except auth_svc.AuthError as exc:
        raise ApiError(_status_for(exc.code), exc.code, exc.message) from exc
    return {"otpauth_uri": uri}


@router.post("/totp/verify")
def totp_verify(
    body: TotpIn,
    response: Response,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        auth_svc.totp_verify(db, user, body.code)
    except auth_svc.AuthError as exc:
        raise ApiError(_status_for(exc.code), exc.code, exc.message) from exc
    # token_version was bumped; re-issue this session's tokens so the caller stays logged in
    access, refresh_raw, _ = auth_svc.issue_tokens(
        db, user, mfa=True, user_agent=request.headers.get("user-agent", "")
    )
    set_session_cookies(response, access, refresh_raw)
    return {"ok": True, "totp_enabled": True}

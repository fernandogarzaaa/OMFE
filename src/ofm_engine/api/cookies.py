"""Session cookie helpers (httpOnly access/refresh, readable CSRF cookie)."""

from __future__ import annotations

from fastapi import Response

from ..security import tokens
from ..settings import get_settings

REFRESH_PATH = "/api/v1/auth"


def set_session_cookies(resp: Response, access: str, refresh: str) -> None:
    s = get_settings()
    secure = s.secure_cookies
    resp.set_cookie(
        tokens.ACCESS_COOKIE,
        access,
        max_age=s.access_token_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    resp.set_cookie(
        tokens.REFRESH_COOKIE,
        refresh,
        max_age=s.refresh_token_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="strict",
        path=REFRESH_PATH,
    )
    set_csrf_cookie(resp, tokens.new_csrf_token())


def set_csrf_cookie(resp: Response, value: str) -> None:
    s = get_settings()
    resp.set_cookie(
        tokens.CSRF_COOKIE,
        value,
        max_age=s.refresh_token_ttl_seconds,
        httponly=False,
        secure=s.secure_cookies,
        samesite="strict",
        path="/",
    )


def clear_session_cookies(resp: Response) -> None:
    resp.delete_cookie(tokens.ACCESS_COOKIE, path="/")
    resp.delete_cookie(tokens.REFRESH_COOKIE, path=REFRESH_PATH)

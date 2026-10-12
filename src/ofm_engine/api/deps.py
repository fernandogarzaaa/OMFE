"""Request dependencies: principal resolution, RBAC, CSRF, tenant context."""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ApiKey, Tenant, User
from ..security import tokens
from ..services.context import TenantContext, load_context
from .errors import ApiError

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Principal:
    tenant_id: uuid.UUID
    role: str  # admin | member | service (API key)
    kind: str  # user | api_key
    user_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None
    mfa: bool = False

    @property
    def actor_id(self) -> uuid.UUID | None:
        return self.user_id or self.api_key_id

    @property
    def is_admin(self) -> bool:
        return self.kind == "user" and self.role == "admin"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def check_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(tokens.CSRF_COOKIE)
    header = request.headers.get(tokens.CSRF_HEADER)
    if not tokens.csrf_matches(cookie, header):
        raise ApiError(403, "csrf_failed", "missing or invalid CSRF token")


def _principal_from_api_key(db: Session, raw: str) -> Principal:
    prefix = tokens.parse_api_key(raw)
    if prefix is None:
        raise ApiError(401, "invalid_api_key", "invalid API key")
    key = db.scalar(select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None)))
    if key is None or not hmac.compare_digest(key.key_hash, tokens.sha256_hex(raw)):
        raise ApiError(401, "invalid_api_key", "invalid API key")
    tenant = db.get(Tenant, key.tenant_id)
    if tenant is None or not tenant.active:
        raise ApiError(401, "invalid_api_key", "invalid API key")
    key.last_used_at = datetime.now(UTC)
    return Principal(tenant_id=key.tenant_id, role="service", kind="api_key", api_key_id=key.id)


def load_user_from_access(db: Session, token: str) -> tuple[User, tokens.AccessClaims]:
    try:
        claims = tokens.decode_access_token(token)
    except tokens.TokenError as exc:
        raise ApiError(401, "unauthorized", "invalid or expired session") from exc
    user = db.get(User, claims.user_id)
    if (
        user is None or user.deleted_at is not None or not user.is_active
        or user.tenant_id != claims.tenant_id or user.token_version != claims.token_version
    ):
        raise ApiError(401, "unauthorized", "session revoked")
    tenant = db.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.active:
        raise ApiError(401, "unauthorized", "tenant inactive")
    return user, claims


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    raw_key = request.headers.get("X-API-Key")
    if raw_key:
        p = _principal_from_api_key(db, raw_key.strip())
        request.state.principal = p
        return p
    token = request.cookies.get(tokens.ACCESS_COOKIE)
    if not token:
        raise ApiError(401, "unauthorized", "authentication required")
    user, claims = load_user_from_access(db, token)
    check_csrf(request)  # cookie-authenticated, state-changing requests need double-submit token
    p = Principal(tenant_id=user.tenant_id, role=user.role, kind="user", user_id=user.id, mfa=claims.mfa)
    request.state.principal = p
    return p


def require_roles(*roles: str) -> Callable[[Principal], Principal]:
    def _dep(p: Principal = Depends(get_principal)) -> Principal:
        if p.role not in roles:
            raise ApiError(403, "forbidden", "insufficient role")
        return p

    return _dep


require_admin = require_roles("admin")
require_member = require_roles("admin", "member", "service")  # business data (users + API keys)
require_user = require_roles("admin", "member")  # human sessions only


def get_current_user(p: Principal = Depends(require_user), db: Session = Depends(get_db)) -> User:
    user = db.get(User, p.user_id)
    assert user is not None
    return user


def tenant_ctx(db: Session, p: Principal) -> TenantContext:
    return load_context(db, tenant_id=p.tenant_id)


def parse_uuid(value: str, what: str = "resource") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ApiError(404, "not_found", f"{what} not found") from exc

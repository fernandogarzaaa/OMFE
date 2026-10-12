"""Authentication flows: login with lockout + TOTP, rotating refresh tokens with reuse detection."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import RefreshToken, Tenant, User
from ..security import crypto, passwords, tokens
from ..settings import get_settings
from . import audit


class AuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Session_:
    user: User
    access_token: str
    refresh_token: str


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def issue_tokens(db: Session, user: User, *, family_id: uuid.UUID | None = None, mfa: bool = False,
                 user_agent: str = "") -> tuple[str, str, RefreshToken]:
    s = get_settings()
    access = tokens.create_access_token(user_id=user.id, tenant_id=user.tenant_id, role=user.role,
                                        token_version=user.token_version, mfa=mfa)
    raw, digest = tokens.new_refresh_token()
    rt = RefreshToken(
        tenant_id=user.tenant_id, user_id=user.id, family_id=family_id or uuid.uuid4(), token_hash=digest,
        expires_at=datetime.now(UTC) + timedelta(seconds=s.refresh_token_ttl_seconds), user_agent=user_agent[:200],
    )
    db.add(rt)
    db.flush()
    return access, raw, rt


def totp_secret(user: User) -> str | None:
    if not user.totp_secret_enc:
        return None
    return crypto.decrypt_str(user.totp_secret_enc, tenant_id=str(user.tenant_id), name=f"totp:{user.id}")


def login(db: Session, email: str, password: str, totp: str | None, *, ip: str = "", user_agent: str = "") -> Session_:
    s = get_settings()
    now = datetime.now(UTC)
    user = db.scalar(select(User).where(User.email == email.strip().lower(), User.deleted_at.is_(None)))
    locked_until = _aware(user.locked_until) if user else None
    if user is not None and locked_until and locked_until > now:
        passwords.verify_password(None, password)  # constant-ish timing
        audit.record(db, tenant_id=user.tenant_id, actor_id=user.id, action="auth.login_locked", ip=ip)
        raise AuthError("account_locked", "too many failed attempts; try again later")
    ok = passwords.verify_password(user.password_hash if user else None, password)
    tenant = db.get(Tenant, user.tenant_id) if user else None
    if not ok or user is None or not user.is_active or tenant is None or not tenant.active:
        if user is not None:
            user.failed_logins += 1
            meta = {"failed_logins": user.failed_logins}
            if user.failed_logins >= s.login_max_attempts:
                user.locked_until = now + timedelta(seconds=s.login_lockout_seconds)
                user.failed_logins = 0
                meta["locked"] = True
            audit.record(db, tenant_id=user.tenant_id, actor_id=user.id, action="auth.login_failed", meta=meta, ip=ip)
        raise AuthError("invalid_credentials", "invalid email or password")
    if user.totp_enabled:
        if not totp:
            raise AuthError("totp_required", "two-factor code required")
        secret = totp_secret(user)
        if not secret or not tokens.verify_totp(secret, totp):
            user.failed_logins += 1
            if user.failed_logins >= s.login_max_attempts:
                user.locked_until = now + timedelta(seconds=s.login_lockout_seconds)
                user.failed_logins = 0
            audit.record(db, tenant_id=user.tenant_id, actor_id=user.id, action="auth.totp_failed", ip=ip)
            raise AuthError("invalid_totp", "invalid two-factor code")
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    access, refresh, _ = issue_tokens(db, user, mfa=user.totp_enabled, user_agent=user_agent)
    audit.record(db, tenant_id=user.tenant_id, actor_id=user.id, action="auth.login", ip=ip)
    return Session_(user, access, refresh)


def revoke_family(db: Session, family_id: uuid.UUID) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


def refresh(db: Session, raw: str | None, *, ip: str = "", user_agent: str = "") -> Session_:
    """Rotate a refresh token. Presenting an already-rotated token revokes its whole family."""
    if not raw:
        raise AuthError("unauthorized", "refresh token missing")
    now = datetime.now(UTC)
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == tokens.sha256_hex(raw)))
    if rt is None:
        raise AuthError("unauthorized", "invalid refresh token")
    if rt.revoked_at is not None:
        if rt.replaced_by is not None:  # reuse of a rotated token => likely theft
            revoke_family(db, rt.family_id)
            audit.record(db, tenant_id=rt.tenant_id, actor_id=rt.user_id, action="auth.refresh_reuse_detected",
                         meta={"family": str(rt.family_id)}, ip=ip)
            raise AuthError("refresh_reused", "refresh token reuse detected; all sessions revoked")
        raise AuthError("unauthorized", "refresh token revoked")
    expires_at = _aware(rt.expires_at)
    assert expires_at is not None
    if expires_at <= now:
        raise AuthError("unauthorized", "refresh token expired")
    user = db.get(User, rt.user_id)
    if user is None or user.deleted_at is not None or not user.is_active:
        revoke_family(db, rt.family_id)
        raise AuthError("unauthorized", "user inactive")
    access, new_raw, new_rt = issue_tokens(db, user, family_id=rt.family_id, mfa=user.totp_enabled,
                                           user_agent=user_agent)
    rt.revoked_at = now
    rt.replaced_by = new_rt.id
    return Session_(user, access, new_raw)


def logout(db: Session, raw: str | None) -> None:
    if not raw:
        return
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == tokens.sha256_hex(raw)))
    if rt is not None:
        revoke_family(db, rt.family_id)
        audit.record(db, tenant_id=rt.tenant_id, actor_id=rt.user_id, action="auth.logout")


def totp_setup(db: Session, user: User) -> str:
    if user.totp_enabled:
        raise AuthError("totp_already_enabled", "two-factor authentication is already enabled")
    secret = tokens.new_totp_secret()
    user.totp_secret_enc = crypto.encrypt_str(secret, tenant_id=str(user.tenant_id), name=f"totp:{user.id}")
    return tokens.totp_uri(secret, user.email, issuer=get_settings().app_name)


def totp_verify(db: Session, user: User, code: str) -> None:
    secret = totp_secret(user)
    if not secret:
        raise AuthError("totp_not_setup", "call /auth/totp/setup first")
    if not tokens.verify_totp(secret, code):
        raise AuthError("invalid_totp", "invalid two-factor code")
    user.totp_enabled = True
    user.token_version += 1  # force re-login on other sessions
    audit.record(db, tenant_id=user.tenant_id, actor_id=user.id, action="auth.totp_enabled")

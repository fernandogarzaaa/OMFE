"""Access (JWT) + refresh (opaque, rotating) tokens, CSRF, API keys, TOTP helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import pyotp

from ..settings import get_settings

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
ALGORITHM = "HS256"
ROLES = ("admin", "member")


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    token_version: int
    mfa: bool


def create_access_token(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str, token_version: int, mfa: bool = False,
    now: datetime | None = None,
) -> str:
    s = get_settings()
    issued = now or datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "role": role,
        "tv": token_version,
        "mfa": mfa,
        "iss": s.jwt_issuer,
        "iat": int(issued.timestamp()),
        "nbf": int(issued.timestamp()),
        "exp": int((issued + timedelta(seconds=s.access_token_ttl_seconds)).timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> AccessClaims:
    s = get_settings()
    try:
        data = jwt.decode(
            token,
            s.jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],  # pinned: rejects alg=none / RS confusion
            issuer=s.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "tid", "role", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if data.get("typ") != "access" or data.get("role") not in ROLES:
        raise TokenError("invalid token type or role")
    try:
        return AccessClaims(
            user_id=uuid.UUID(data["sub"]),
            tenant_id=uuid.UUID(data["tid"]),
            role=data["role"],
            token_version=int(data.get("tv", 0)),
            mfa=bool(data.get("mfa", False)),
        )
    except (ValueError, TypeError) as exc:
        raise TokenError("malformed claims") from exc


def new_refresh_token() -> tuple[str, str]:
    """Return (plaintext, sha256-hash). Only the hash is persisted."""
    raw = secrets.token_urlsafe(48)
    return raw, sha256_hex(raw)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(cookie_value: str | None, header_value: str | None) -> bool:
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)


# --- API keys ----------------------------------------------------------------
def new_api_key() -> tuple[str, str, str]:
    """Return (plaintext, prefix, hash). Plaintext format: ofm_<prefix>_<secret>."""
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    plaintext = f"ofm_{prefix}_{secret}"
    return plaintext, prefix, sha256_hex(plaintext)


def parse_api_key(value: str) -> str | None:
    """Return the prefix of a well-formed key, else None."""
    parts = value.split("_", 2)
    if len(parts) != 3 or parts[0] != "ofm" or len(parts[1]) != 12 or len(parts[2]) < 20:
        return None
    return parts[1]


# --- TOTP ----------------------------------------------------------------------
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str, issuer: str = "OFM Engine") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str, code: str, at: datetime | None = None) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    totp = pyotp.TOTP(secret)
    return bool(totp.verify(code, for_time=at, valid_window=1))

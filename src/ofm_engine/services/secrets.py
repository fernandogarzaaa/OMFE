"""Tenant secret storage (envelope-encrypted) and resolution (DB first, env fallback)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Tenant, TenantSecret, User, WebhookSubscription
from ..security import crypto
from ..settings import env_secret
from ..tenant_config import SECRET_NAMES, env_var_name


class UnknownSecretError(ValueError):
    pass


@dataclass(frozen=True)
class SecretStatus:
    name: str
    configured: bool
    source: str  # "vault" | "env" | "none"
    hint: str
    env_var: str


def _validate_name(name: str) -> None:
    if name not in SECRET_NAMES:
        raise UnknownSecretError(f"unknown secret {name!r}")


def set_secret(db: Session, tenant: Tenant, name: str, value: str, actor_id: uuid.UUID | None = None) -> None:
    _validate_name(name)
    value = value.strip()
    if not value or len(value) > 4096:
        raise ValueError("secret value must be 1..4096 chars")
    env = crypto.encrypt(value, tenant_id=str(tenant.id), name=name)
    row = db.scalar(
        select(TenantSecret).where(TenantSecret.tenant_id == tenant.id, TenantSecret.name == name)
    )
    hint = value[-2:] if len(value) >= 12 else ""
    if row is None:
        row = TenantSecret(
            tenant_id=tenant.id,
            name=name,
            wrapped_dek=env.wrapped_dek,
            nonce=env.nonce,
            ciphertext=env.ciphertext,
            hint=hint,
            updated_by=actor_id,
        )
        db.add(row)
    else:
        row.wrapped_dek, row.nonce, row.ciphertext = env.wrapped_dek, env.nonce, env.ciphertext
        row.hint = hint
        row.updated_by = actor_id
    db.flush()


def delete_secret(db: Session, tenant: Tenant, name: str) -> bool:
    _validate_name(name)
    row = db.scalar(
        select(TenantSecret).where(TenantSecret.tenant_id == tenant.id, TenantSecret.name == name)
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def get_secret(db: Session, tenant: Tenant, name: str) -> str | None:
    _validate_name(name)
    row = db.scalar(
        select(TenantSecret).where(TenantSecret.tenant_id == tenant.id, TenantSecret.name == name)
    )
    if row is not None:
        return crypto.decrypt(
            crypto.Envelope(row.wrapped_dek, row.nonce, row.ciphertext), tenant_id=str(tenant.id), name=name
        )
    return env_secret(env_var_name(tenant.slug, name))


def secret_statuses(db: Session, tenant: Tenant) -> list[SecretStatus]:
    rows = {
        r.name: r for r in db.scalars(select(TenantSecret).where(TenantSecret.tenant_id == tenant.id)).all()
    }
    out = []
    for name in SECRET_NAMES:
        var = env_var_name(tenant.slug, name)
        if name in rows:
            out.append(SecretStatus(name, True, "vault", rows[name].hint, var))
        elif env_secret(var):
            out.append(SecretStatus(name, True, "env", "", var))
        else:
            out.append(SecretStatus(name, False, "none", "", var))
    return out


def rotate_all(db: Session, master_keys: str | None = None) -> int:
    """Re-wrap every stored DEK (and compact encrypted fields) with the primary master key."""
    count = 0
    for row in db.scalars(select(TenantSecret)).all():
        new = crypto.rewrap(crypto.Envelope(row.wrapped_dek, row.nonce, row.ciphertext), master_keys)
        row.wrapped_dek = new.wrapped_dek
        count += 1
    for user in db.scalars(select(User).where(User.totp_secret_enc.is_not(None))).all():
        assert user.totp_secret_enc is not None
        user.totp_secret_enc = crypto.rewrap_str(user.totp_secret_enc, master_keys)
        count += 1
    for sub in db.scalars(select(WebhookSubscription)).all():
        sub.secret_enc = crypto.rewrap_str(sub.secret_enc, master_keys)
        count += 1
    db.flush()
    return count

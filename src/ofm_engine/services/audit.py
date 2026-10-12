"""Append-only audit log for security-relevant and admin actions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..hashing import keyed_hash
from ..logging_setup import redact
from ..models import AuditLog
from ..settings import get_settings


def record(
    db: Session,
    *,
    tenant_id: uuid.UUID | None,
    action: str,
    actor_id: uuid.UUID | None = None,
    actor_type: str = "user",
    target: str = "",
    meta: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    ip_hash = keyed_hash(ip, get_settings().pii_hash_salt.get_secret_value()) if ip else ""
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            target=target[:200],
            meta=redact(meta or {}),
            ip_hash=ip_hash,
        )
    )

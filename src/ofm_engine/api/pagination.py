"""Keyset pagination: ?limit=&cursor= -> {"items": [...], "next_cursor": str | None}."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import Session

from .errors import ApiError

T = TypeVar("T")
MAX_LIMIT = 200


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        pad = "=" * (-len(cursor) % 4)
        ts, rid = json.loads(base64.urlsafe_b64decode(cursor + pad))
        return datetime.fromisoformat(ts), uuid.UUID(rid)
    except (ValueError, TypeError) as exc:
        raise ApiError(400, "invalid_cursor", "cursor is malformed") from exc


def paginate(
    db: Session, stmt: Select[Any], model: Any, limit: int, cursor: str | None, serialize: Callable[[Any], T]
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), MAX_LIMIT))
    if cursor:
        ts, rid = decode_cursor(cursor)
        if ts.tzinfo is not None and db.get_bind().dialect.name == "sqlite":
            ts = ts.replace(tzinfo=None)
        stmt = stmt.where(or_(model.created_at < ts, and_(model.created_at == ts, model.id < rid)))
    rows: Sequence[Any] = db.scalars(
        stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    nxt = encode_cursor(rows[-1].created_at, rows[-1].id) if has_more and rows else None
    return {"items": [serialize(r) for r in rows], "next_cursor": nxt}

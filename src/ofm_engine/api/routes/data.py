"""/api/v1/revenue, /api/v1/kpis, /api/v1/live/stream."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ...db import get_db, session_factory
from ...models import RevenueRecord
from ...services import audit, live
from ...services import revenue as revenue_svc
from ...services.kpis import get_kpis
from ...tenant_config import RevenueMapping
from ..deps import Principal, client_ip, parse_uuid, require_member, tenant_ctx
from ..errors import ApiError
from ..pagination import paginate
from ..serializers import iso, revenue_out

router = APIRouter(tags=["data"])

# Friendly aliases accepted in the import mapping (canonical keys: see RevenueMapping).
MAPPING_ALIASES = {"amount": "gross", "subs": "new_subs"}


@router.post("/revenue/import")
async def import_revenue(
    request: Request,
    file: UploadFile = File(...),
    mapping: str = Form(default="{}"),
    p: Principal = Depends(require_member),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    content = await file.read(revenue_svc.MAX_BYTES + 1)
    try:
        raw_map = json.loads(mapping or "{}")
        if not isinstance(raw_map, dict):
            raise ValueError("mapping must be a JSON object")
        base = tenant_ctx(db, p).config.revenue_mapping.model_dump()
        given: dict[str, Any] = {
            str(MAPPING_ALIASES.get(str(k), str(k))): v for k, v in raw_map.items() if v not in (None, "")
        }
        unknown = sorted(set(given) - set(base))
        if unknown:
            raise ValueError(f"unknown mapping key(s) {unknown}; allowed {sorted(base)}")
        cfg = RevenueMapping.model_validate({**base, **given})
    except (ValueError, ValidationError) as exc:
        raise ApiError(422, "invalid_mapping", f"mapping: {str(exc)[:200]}") from exc
    try:
        res = await run_in_threadpool(
            revenue_svc.import_csv, db, p.tenant_id, content, cfg, (file.filename or "upload.csv")[:200]
        )
    except revenue_svc.ImportError_ as exc:
        raise ApiError(422, "invalid_csv", str(exc)) from exc
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        actor_type=p.kind,
        action="revenue.import",
        meta={"imported": res.imported, "duplicates": res.duplicates},
        ip=client_ip(request),
    )
    return res.as_dict()


@router.get("/revenue")
def list_revenue(
    creator_id: str | None = None,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    limit: int = 100,
    cursor: str | None = None,
    p: Principal = Depends(require_member),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(RevenueRecord).where(RevenueRecord.tenant_id == p.tenant_id)
    if creator_id:
        stmt = stmt.where(RevenueRecord.creator_id == parse_uuid(creator_id, "creator"))
    if from_:
        stmt = stmt.where(RevenueRecord.day >= from_)
    if to:
        stmt = stmt.where(RevenueRecord.day <= to)
    return paginate(db, stmt, RevenueRecord, limit, cursor, revenue_out)


@router.get("/kpis")
def kpis(
    range: Literal["7d", "30d", "90d"] = "30d",
    p: Principal = Depends(require_member),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return get_kpis(db, p.tenant_id, range)


def _sse(event: str, data: dict[str, Any], event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'), default=str)}\n\n"


def _kpi_delta(rows: list[Any]) -> dict[str, int]:
    d = {"clicks": 0, "leads": 0, "signed": 0, "subs": 0, "revenue_cents": 0}
    for r in rows:
        data = r.data or {}
        if r.kind == "click":
            d["clicks"] += 1
        elif r.kind == "lead":
            d["leads"] += 1
        elif r.kind == "lead_stage" and data.get("stage") == "signed":
            d["signed"] += 1
        elif r.kind == "revenue_import":
            d["revenue_cents"] += int(data.get("revenue_cents", 0))
            d["subs"] += int(data.get("subs", 0))
    return d


EVENT_TYPES = {
    "click": "click",
    "lead": "lead",
    "lead_stage": "lead",
    "capi_sent": "capi_sent",
    "revenue_import": "revenue_import",
    "job_failed": "job_failed",
}


def _fetch(tenant_id: Any, since: int) -> tuple[list[Any], int]:
    db = session_factory()()
    try:
        if since < 0:
            return [], live.latest_seq(db, tenant_id)
        rows = live.fetch_since(db, tenant_id, since)
        for r in rows:
            db.expunge(r)
        return rows, since
    finally:
        db.close()


@router.get("/live/stream")
async def live_stream(
    request: Request, max_seconds: float = 300.0, p: Principal = Depends(require_member)
) -> StreamingResponse:
    """Server-Sent Events. Resumable with Last-Event-ID; ends after `max_seconds` (clients reconnect)."""
    max_seconds = max(0.5, min(max_seconds, 3600.0))
    last_id = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    try:
        since = int(last_id) if last_id else -1
    except ValueError:
        since = -1
    tenant_id = p.tenant_id
    tid = str(tenant_id)

    async def gen() -> Any:
        nonlocal since
        loop = asyncio.get_running_loop()
        live.broker.bind_loop(loop)
        deadline = loop.time() + max_seconds
        if since < 0:
            _, since = await run_in_threadpool(_fetch, tenant_id, -1)
        yield "retry: 3000\n: connected\n\n"
        last_beat = loop.time()
        while loop.time() < deadline:
            rows, _ = await run_in_threadpool(_fetch, tenant_id, since)
            if rows:
                for r in rows:
                    data = dict(r.data or {})
                    yield _sse(
                        "event",
                        {"type": EVENT_TYPES.get(r.kind, r.kind), "payload": data, "at": iso(r.created_at)},
                        r.seq,
                    )
                    since = r.seq
                yield _sse("kpi", _kpi_delta(rows))
                continue
            if await request.is_disconnected():
                break
            if loop.time() - last_beat > 15:
                yield ": keepalive\n\n"
                last_beat = loop.time()
            await live.broker.wait(tid, timeout=min(2.0, max(0.05, deadline - loop.time())))

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )

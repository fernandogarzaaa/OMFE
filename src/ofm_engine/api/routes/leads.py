"""/api/v1/leads, /api/v1/pipeline."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Lead
from ...services import audit
from ...services import leads as lead_svc
from ..deps import Principal, client_ip, parse_uuid, require_member, tenant_ctx
from ..errors import not_found
from ..pagination import paginate
from ..serializers import LEAD_STAGES, lead_out

router = APIRouter(tags=["leads"])
Stage = Literal["new", "contacted", "call_booked", "signed", "lost"]


class LeadPatch(BaseModel):
    stage: Stage | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)


def get_lead(db: Session, p: Principal, lead_id: str) -> Lead:
    lead = db.scalar(
        select(Lead).where(
            Lead.tenant_id == p.tenant_id, Lead.id == parse_uuid(lead_id, "lead"), Lead.deleted_at.is_(None)
        )
    )
    if lead is None:
        raise not_found("lead")
    return lead


@router.get("/leads")
def list_leads(
    stage: Stage | None = None,
    tier: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    p: Principal = Depends(require_member),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Lead).where(Lead.tenant_id == p.tenant_id, Lead.deleted_at.is_(None))
    if stage:
        stmt = stmt.where(Lead.stage == stage)
    if tier:
        stmt = stmt.where(Lead.tier == tier)
    return paginate(db, stmt, Lead, limit, cursor, lead_out)


@router.get("/leads/{lead_id}")
def read_lead(
    lead_id: str, p: Principal = Depends(require_member), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return lead_out(get_lead(db, p, lead_id))


@router.patch("/leads/{lead_id}")
def patch_lead(
    lead_id: str,
    body: LeadPatch,
    request: Request,
    p: Principal = Depends(require_member),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    lead = get_lead(db, p, lead_id)
    data = body.model_dump(exclude_none=True)
    for key in ("name", "phone", "timezone"):
        if key in data:
            setattr(lead, key, data[key])
    if "email" in data:
        lead.email = str(data["email"]).lower()
    if "stage" in data:
        lead_svc.change_stage(tenant_ctx(db, p), lead, data["stage"])
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        actor_type=p.kind,
        action="lead.update",
        target=str(lead.id),
        meta={"fields": sorted(data), "stage": data.get("stage")},
        ip=client_ip(request),
    )
    return lead_out(lead)


@router.post("/leads/{lead_id}/rescore")
def rescore(
    lead_id: str, p: Principal = Depends(require_member), db: Session = Depends(get_db)
) -> dict[str, Any]:
    lead = get_lead(db, p, lead_id)
    lead_svc.rescore(tenant_ctx(db, p), lead)
    return lead_out(lead)


@router.get("/pipeline")
def pipeline(
    per_stage: int = 50, p: Principal = Depends(require_member), db: Session = Depends(get_db)
) -> dict[str, Any]:
    per_stage = max(1, min(per_stage, 200))
    counts = dict(
        db.execute(
            select(Lead.stage, func.count())
            .where(Lead.tenant_id == p.tenant_id, Lead.deleted_at.is_(None))
            .group_by(Lead.stage)
        ).all()
    )
    stages = []
    for key, label in LEAD_STAGES:
        rows = db.scalars(
            select(Lead)
            .where(Lead.tenant_id == p.tenant_id, Lead.deleted_at.is_(None), Lead.stage == key)
            .order_by(Lead.score.desc(), Lead.created_at.desc())
            .limit(per_stage)
        ).all()
        stages.append(
            {
                "key": key,
                "label": label,
                "count": int(counts.get(key, 0)),
                "leads": [lead_out(r) for r in rows],
            }
        )
    return {"stages": stages}

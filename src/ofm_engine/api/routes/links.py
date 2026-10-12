"""/api/v1/links — tracking links."""

from __future__ import annotations

import re
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...attribution import safe_destination
from ...db import get_db
from ...models import Creator, TrackingLink, utcnow
from ...services import audit
from ...services.tracking import link_click_counts
from ..deps import Principal, client_ip, parse_uuid, require_admin, require_member
from ..errors import ApiError, not_found
from ..pagination import paginate
from ..serializers import link_out

router = APIRouter(prefix="/links", tags=["links"])
SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{1,63}$")


def _dest(v: str) -> str:
    if not safe_destination(v):
        raise ValueError("destination_url must be an absolute http(s) URL")
    return v


class LinkIn(BaseModel):
    slug: str | None = Field(default=None, max_length=64)
    creator_id: str | None = None
    channel: str = Field(min_length=1, max_length=32)
    destination_url: str = Field(min_length=8, max_length=1000)
    campaign: str = Field(min_length=1, max_length=120)
    capi_on_click: bool = False

    @field_validator("destination_url")
    @classmethod
    def _v_dest(cls, v: str) -> str:
        return _dest(v)

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str | None) -> str | None:
        if v is not None and not SLUG_RE.fullmatch(v):
            raise ValueError("slug may contain letters, digits, '-' and '_' (2-64 chars)")
        return v


class LinkPatch(BaseModel):
    channel: str | None = Field(default=None, min_length=1, max_length=32)
    destination_url: str | None = Field(default=None, min_length=8, max_length=1000)
    campaign: str | None = Field(default=None, min_length=1, max_length=120)
    creator_id: str | None = None
    capi_on_click: bool | None = None
    active: bool | None = None

    @field_validator("destination_url")
    @classmethod
    def _d(cls, v: str | None) -> str | None:
        return None if v is None else _dest(v)


def _creator_id(db: Session, p: Principal, raw: str | None) -> Any:
    if not raw:
        return None
    cid = parse_uuid(raw, "creator")
    if (
        db.scalar(
            select(Creator.id).where(
                Creator.tenant_id == p.tenant_id, Creator.id == cid, Creator.deleted_at.is_(None)
            )
        )
        is None
    ):
        raise ApiError(422, "validation_error", "creator_id: creator not found")
    return cid


def get_link(db: Session, p: Principal, link_id: str) -> TrackingLink:
    link = db.scalar(
        select(TrackingLink).where(
            TrackingLink.tenant_id == p.tenant_id,
            TrackingLink.id == parse_uuid(link_id, "link"),
            TrackingLink.deleted_at.is_(None),
        )
    )
    if link is None:
        raise not_found("link")
    return link


@router.get("")
def list_links(
    creator_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    p: Principal = Depends(require_member),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(TrackingLink).where(
        TrackingLink.tenant_id == p.tenant_id, TrackingLink.deleted_at.is_(None)
    )
    if creator_id:
        stmt = stmt.where(TrackingLink.creator_id == parse_uuid(creator_id, "creator"))
    page = paginate(db, stmt, TrackingLink, limit, cursor, lambda link: link)
    counts = link_click_counts(db, p.tenant_id, [lk.id for lk in page["items"]])
    page["items"] = [link_out(lk, counts.get(lk.id, 0)) for lk in page["items"]]
    return page


@router.post("", status_code=201)
def create_link(
    body: LinkIn, request: Request, p: Principal = Depends(require_member), db: Session = Depends(get_db)
) -> dict[str, Any]:
    slug = body.slug or secrets.token_urlsafe(6).replace("_", "x").replace("-", "y")
    if db.scalar(select(TrackingLink.id).where(TrackingLink.slug == slug)) is not None:
        raise ApiError(409, "slug_taken", "this slug is already in use")
    link = TrackingLink(
        tenant_id=p.tenant_id,
        slug=slug,
        creator_id=_creator_id(db, p, body.creator_id),
        channel=body.channel.strip().lower(),
        destination_url=body.destination_url,
        campaign=body.campaign.strip(),
        capi_on_click=body.capi_on_click,
    )
    db.add(link)
    db.flush()
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        actor_type=p.kind,
        action="link.create",
        target=str(link.id),
        meta={"slug": slug},
        ip=client_ip(request),
    )
    return link_out(link)


@router.patch("/{link_id}")
def patch_link(
    link_id: str,
    body: LinkPatch,
    request: Request,
    p: Principal = Depends(require_member),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    link = get_link(db, p, link_id)
    data = body.model_dump(exclude_unset=True)
    if "creator_id" in data:
        link.creator_id = _creator_id(db, p, data.pop("creator_id"))
    for key, val in data.items():
        if val is not None:
            setattr(link, key, val.strip().lower() if key == "channel" else val)
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        actor_type=p.kind,
        action="link.update",
        target=str(link.id),
        meta={"fields": sorted(data)},
        ip=client_ip(request),
    )
    return link_out(link, link_click_counts(db, p.tenant_id, [link.id]).get(link.id, 0))


@router.delete("/{link_id}")
def delete_link(
    link_id: str, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    link = get_link(db, p, link_id)
    link.deleted_at = utcnow()
    link.active = False
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        action="link.delete",
        target=str(link.id),
        ip=client_ip(request),
    )
    return {"ok": True}

"""/api/v1/creators."""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Creator, utcnow
from ...services import audit
from ..deps import Principal, client_ip, parse_uuid, require_admin, require_member
from ..errors import ApiError, not_found
from ..pagination import paginate
from ..serializers import creator_out

router = APIRouter(prefix="/creators", tags=["creators"])
HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
Status = Literal["active", "paused", "onboarding"]


class BioLink(BaseModel):
    label: str = Field(max_length=80)
    url: str = Field(max_length=1000)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        from ...attribution import safe_destination

        if not (safe_destination(v) or v.startswith("/l/")):
            raise ValueError("url must be http(s) or a /l/ tracking link")
        return v


class CreatorIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    handle: str = Field(min_length=1, max_length=64)
    status: Status = "onboarding"
    channels: list[str] = Field(default_factory=list, max_length=20)
    bio: str = Field(default="", max_length=500)
    avatar_url: str = Field(default="", max_length=500)
    bio_links: list[BioLink] = Field(default_factory=list, max_length=20)

    @field_validator("handle")
    @classmethod
    def _handle(cls, v: str) -> str:
        v = v.strip().lower().lstrip("@")
        if not HANDLE_RE.fullmatch(v):
            raise ValueError("handle may contain lowercase letters, digits, '-' and '_'")
        return v


class CreatorPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Status | None = None
    channels: list[str] | None = Field(default=None, max_length=20)
    bio: str | None = Field(default=None, max_length=500)
    avatar_url: str | None = Field(default=None, max_length=500)
    bio_links: list[BioLink] | None = Field(default=None, max_length=20)


def get_creator(db: Session, p: Principal, creator_id: str) -> Creator:
    c = db.scalar(
        select(Creator).where(
            Creator.tenant_id == p.tenant_id,
            Creator.id == parse_uuid(creator_id, "creator"),
            Creator.deleted_at.is_(None),
        )
    )
    if c is None:
        raise not_found("creator")
    return c


@router.get("")
def list_creators(
    limit: int = 50,
    cursor: str | None = None,
    status: Status | None = None,
    p: Principal = Depends(require_member),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Creator).where(Creator.tenant_id == p.tenant_id, Creator.deleted_at.is_(None))
    if status:
        stmt = stmt.where(Creator.status == status)
    return paginate(db, stmt, Creator, limit, cursor, creator_out)


@router.post("", status_code=201)
def create_creator(
    body: CreatorIn, request: Request, p: Principal = Depends(require_member), db: Session = Depends(get_db)
) -> dict[str, Any]:
    if db.scalar(select(Creator).where(Creator.tenant_id == p.tenant_id, Creator.slug == body.handle)):
        raise ApiError(409, "handle_taken", "a creator with this handle already exists")
    c = Creator(
        tenant_id=p.tenant_id,
        slug=body.handle,
        display_name=body.name,
        status=body.status,
        channels=[ch.strip().lower()[:32] for ch in body.channels if ch.strip()],
        bio=body.bio,
        avatar_url=body.avatar_url,
        bio_links=[b.model_dump() for b in body.bio_links],
    )
    db.add(c)
    db.flush()
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        actor_type=p.kind,
        action="creator.create",
        target=str(c.id),
        ip=client_ip(request),
    )
    return creator_out(c)


@router.get("/{creator_id}")
def read_creator(
    creator_id: str, p: Principal = Depends(require_member), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return creator_out(get_creator(db, p, creator_id))


@router.patch("/{creator_id}")
def patch_creator(
    creator_id: str,
    body: CreatorPatch,
    request: Request,
    p: Principal = Depends(require_member),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    c = get_creator(db, p, creator_id)
    data = body.model_dump(exclude_none=True)
    if "name" in data:
        c.display_name = data["name"]
    for key in ("status", "bio", "avatar_url"):
        if key in data:
            setattr(c, key, data[key])
    if "channels" in data:
        c.channels = [ch.strip().lower()[:32] for ch in data["channels"] if ch.strip()]
    if "bio_links" in data:
        c.bio_links = data["bio_links"]
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        actor_type=p.kind,
        action="creator.update",
        target=str(c.id),
        meta={"fields": sorted(data)},
        ip=client_ip(request),
    )
    return creator_out(c)


@router.delete("/{creator_id}")
def delete_creator(
    creator_id: str, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    c = get_creator(db, p, creator_id)
    c.deleted_at = utcnow()
    c.slug = f"{c.slug[:40]}-deleted-{c.id.hex[:8]}"
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        action="creator.delete",
        target=str(c.id),
        ip=client_ip(request),
    )
    return {"ok": True}

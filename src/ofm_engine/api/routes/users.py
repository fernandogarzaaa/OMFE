"""/api/v1/users (admin only)."""

from __future__ import annotations

import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import User, utcnow
from ...security import passwords
from ...services import audit
from ..deps import Principal, client_ip, parse_uuid, require_admin
from ..errors import ApiError, not_found
from ..pagination import paginate
from ..serializers import user_out

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(default="", max_length=200)
    role: Literal["admin", "member"] = "member"
    password: str | None = Field(default=None, max_length=128)


class UserPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    role: Literal["admin", "member"] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, max_length=128)


def _get(db: Session, p: Principal, user_id: str) -> User:
    u = db.scalar(select(User).where(User.tenant_id == p.tenant_id, User.id == parse_uuid(user_id, "user"),
                                     User.deleted_at.is_(None)))
    if u is None:
        raise not_found("user")
    return u


def _check_password(pw: str, email: str) -> None:
    problems = passwords.password_problems(pw, email)
    if problems:
        raise ApiError(422, "weak_password", "password " + "; ".join(problems))


def _admins(db: Session, p: Principal) -> int:
    return int(db.scalar(select(func.count()).select_from(User).where(
        User.tenant_id == p.tenant_id, User.role == "admin", User.deleted_at.is_(None), User.is_active.is_(True))) or 0)


@router.get("")
def list_users(limit: int = 50, cursor: str | None = None, p: Principal = Depends(require_admin),
               db: Session = Depends(get_db)) -> dict[str, Any]:
    stmt = select(User).where(User.tenant_id == p.tenant_id, User.deleted_at.is_(None))
    return paginate(db, stmt, User, limit, cursor, user_out)


@router.post("", status_code=201)
def create_user(body: UserCreate, request: Request, p: Principal = Depends(require_admin),
                db: Session = Depends(get_db)) -> dict[str, Any]:
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise ApiError(409, "email_taken", "a user with this email already exists")
    temp = None
    pw = body.password
    if pw:
        _check_password(pw, email)
    else:
        pw = temp = secrets.token_urlsafe(14)
    u = User(tenant_id=p.tenant_id, email=email, name=body.name, role=body.role,
             password_hash=passwords.hash_password(pw))
    db.add(u)
    db.flush()
    audit.record(db, tenant_id=p.tenant_id, actor_id=p.actor_id, action="user.create", target=str(u.id),
                 meta={"role": body.role}, ip=client_ip(request))
    out = user_out(u)
    if temp:
        out["temporary_password"] = temp
    return out


@router.patch("/{user_id}")
def patch_user(user_id: str, body: UserPatch, request: Request, p: Principal = Depends(require_admin),
               db: Session = Depends(get_db)) -> dict[str, Any]:
    u = _get(db, p, user_id)
    demoting = (body.role == "member" and u.role == "admin") or (body.is_active is False and u.role == "admin")
    if demoting and _admins(db, p) <= 1:
        raise ApiError(409, "last_admin", "cannot remove the last admin")
    if body.name is not None:
        u.name = body.name
    if body.role is not None and body.role != u.role:
        u.role = body.role
        u.token_version += 1
    if body.is_active is not None:
        u.is_active = body.is_active
        if not body.is_active:
            u.token_version += 1
    if body.password:
        _check_password(body.password, u.email)
        u.password_hash = passwords.hash_password(body.password)
        u.token_version += 1
    audit.record(db, tenant_id=p.tenant_id, actor_id=p.actor_id, action="user.update", target=str(u.id),
                 meta={k: v for k, v in body.model_dump(exclude_none=True).items() if k != "password"},
                 ip=client_ip(request))
    return user_out(u)


@router.delete("/{user_id}")
def delete_user(user_id: str, request: Request, p: Principal = Depends(require_admin),
                db: Session = Depends(get_db)) -> dict[str, Any]:
    u = _get(db, p, user_id)
    if u.id == p.user_id:
        raise ApiError(409, "cannot_delete_self", "you cannot delete your own account")
    if u.role == "admin" and _admins(db, p) <= 1:
        raise ApiError(409, "last_admin", "cannot remove the last admin")
    u.deleted_at = utcnow()
    u.is_active = False
    u.token_version += 1
    u.email = f"deleted+{u.id.hex}@invalid.local"
    audit.record(db, tenant_id=p.tenant_id, actor_id=p.actor_id, action="user.delete", target=str(u.id),
                 ip=client_ip(request))
    return {"ok": True}

"""/api/v1/automations, /api-keys, /ai/copy, /integrations, /audit."""

from __future__ import annotations

import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import ApiKey, AuditLog, OutboxJob, WebhookSubscription, utcnow
from ...security import crypto, tokens
from ...security.ssrf import UnsafeURLError, check_webhook_url
from ...services import ai_copy, audit, jobs
from ...services import integrations as integ
from ..deps import Principal, client_ip, parse_uuid, require_admin, require_member, tenant_ctx
from ..errors import ApiError, not_found
from ..pagination import paginate
from ..serializers import api_key_out, audit_out, iso, job_out

router = APIRouter(tags=["admin"])

WEBHOOK_EVENTS = ("lead.created", "lead.stage_changed", "revenue.imported", "*")


# --------------------------------------------------------------------------- webhooks
class WebhookIn(BaseModel):
    url: str = Field(min_length=10, max_length=1000)
    events: list[str] = Field(min_length=1, max_length=10)


def _webhook_out(w: WebhookSubscription) -> dict[str, Any]:
    return {
        "id": str(w.id),
        "url": w.url,
        "events": list(w.events or []),
        "active": w.active,
        "created_at": iso(w.created_at),
    }


@router.get("/automations/webhooks")
def list_webhooks(
    limit: int = 50,
    cursor: str | None = None,
    p: Principal = Depends(require_member),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(WebhookSubscription).where(
        WebhookSubscription.tenant_id == p.tenant_id, WebhookSubscription.deleted_at.is_(None)
    )
    return paginate(db, stmt, WebhookSubscription, limit, cursor, _webhook_out)


@router.post("/automations/webhooks", status_code=201)
def create_webhook(
    body: WebhookIn, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    bad = [e for e in body.events if e not in WEBHOOK_EVENTS]
    if bad:
        raise ApiError(
            422, "validation_error", f"events: unknown event(s) {bad}; allowed {list(WEBHOOK_EVENTS)}"
        )
    try:
        check_webhook_url(body.url)
    except UnsafeURLError as exc:
        raise ApiError(422, "unsafe_url", str(exc)) from exc
    secret = "whsec_" + secrets.token_urlsafe(32)
    sub = WebhookSubscription(
        tenant_id=p.tenant_id, url=body.url, events=sorted(set(body.events)), secret_enc=""
    )
    db.add(sub)
    db.flush()
    sub.secret_enc = crypto.encrypt_str(secret, tenant_id=str(p.tenant_id), name=f"webhook:{sub.id}")
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        action="webhook.create",
        target=str(sub.id),
        meta={"events": body.events},
        ip=client_ip(request),
    )
    return {**_webhook_out(sub), "secret": secret}


@router.delete("/automations/webhooks/{webhook_id}")
def delete_webhook(
    webhook_id: str, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    sub = db.scalar(
        select(WebhookSubscription).where(
            WebhookSubscription.tenant_id == p.tenant_id,
            WebhookSubscription.id == parse_uuid(webhook_id, "webhook"),
            WebhookSubscription.deleted_at.is_(None),
        )
    )
    if sub is None:
        raise not_found("webhook")
    sub.deleted_at = utcnow()
    sub.active = False
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        action="webhook.delete",
        target=str(sub.id),
        ip=client_ip(request),
    )
    return {"ok": True}


@router.get("/automations/jobs")
def list_jobs(
    status: Literal["queued", "running", "retry", "succeeded", "dead"] | None = None,
    kind: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    p: Principal = Depends(require_member),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(OutboxJob).where(OutboxJob.tenant_id == p.tenant_id)
    if status:
        stmt = stmt.where(OutboxJob.status == status)
    if kind:
        stmt = stmt.where(OutboxJob.kind == kind)
    return paginate(db, stmt, OutboxJob, limit, cursor, job_out)


@router.post("/automations/jobs/{job_id}/retry")
def retry_job(
    job_id: str, request: Request, p: Principal = Depends(require_member), db: Session = Depends(get_db)
) -> dict[str, Any]:
    job = db.scalar(
        select(OutboxJob).where(OutboxJob.tenant_id == p.tenant_id, OutboxJob.id == parse_uuid(job_id, "job"))
    )
    if job is None:
        raise not_found("job")
    if job.status not in ("dead", "retry"):
        raise ApiError(409, "not_retryable", f"job is {job.status}")
    jobs.retry_now(db, job)
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        actor_type=p.kind,
        action="job.retry",
        target=str(job.id),
        ip=client_ip(request),
    )
    return job_out(job)


# --------------------------------------------------------------------------- API keys
class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/api-keys")
def list_api_keys(
    limit: int = 50,
    cursor: str | None = None,
    p: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(ApiKey).where(ApiKey.tenant_id == p.tenant_id, ApiKey.revoked_at.is_(None))
    return paginate(db, stmt, ApiKey, limit, cursor, api_key_out)


@router.post("/api-keys", status_code=201)
def create_api_key(
    body: ApiKeyIn, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    plaintext, prefix, digest = tokens.new_api_key()
    key = ApiKey(tenant_id=p.tenant_id, name=body.name, prefix=prefix, key_hash=digest, created_by=p.user_id)
    db.add(key)
    db.flush()
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        action="api_key.create",
        target=str(key.id),
        meta={"name": body.name, "prefix": prefix},
        ip=client_ip(request),
    )
    return {**api_key_out(key), "key": plaintext}


@router.delete("/api-keys/{key_id}")
def revoke_api_key(
    key_id: str, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    key = db.scalar(
        select(ApiKey).where(
            ApiKey.tenant_id == p.tenant_id,
            ApiKey.id == parse_uuid(key_id, "api key"),
            ApiKey.revoked_at.is_(None),
        )
    )
    if key is None:
        raise not_found("api key")
    key.revoked_at = utcnow()
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        action="api_key.revoke",
        target=str(key.id),
        ip=client_ip(request),
    )
    return {"ok": True}


# --------------------------------------------------------------------------- AI copy
class CopyIn(BaseModel):
    kind: Literal["recruitment_ad", "headline", "followup_sms", "followup_email", "social_caption"]
    brief: str = Field(min_length=3, max_length=1000)
    variants: int = Field(default=3, ge=1, le=5)


@router.post("/ai/copy")
def ai_copy_endpoint(
    body: CopyIn, request: Request, p: Principal = Depends(require_member), db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        res = ai_copy.generate(tenant_ctx(db, p), body.kind, body.brief, body.variants)
    except ai_copy.ContentRefused as exc:
        audit.record(
            db,
            tenant_id=p.tenant_id,
            actor_id=p.actor_id,
            actor_type=p.kind,
            action="ai.copy_refused",
            meta={"kind": body.kind, "reason": exc.reason},
            ip=client_ip(request),
        )
        db.commit()
        raise ApiError(422, "content_refused", exc.reason) from exc
    return {"variants": res.variants, "provider": res.provider, "mock": res.mock}


# --------------------------------------------------------------------------- integrations
Provider = Literal["ghl", "meta_capi", "tiktok", "llm"]


class IntegrationIn(BaseModel):
    fields: dict[str, str | None]


@router.get("/integrations")
def list_integrations(
    p: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return integ.status(tenant_ctx(db, p))


@router.put("/integrations/{provider}")
def put_integration(
    provider: Provider,
    body: IntegrationIn,
    request: Request,
    p: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ctx = tenant_ctx(db, p)
    try:
        changed = integ.update(ctx, provider, body.fields, p.user_id)
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc)) from exc
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        action="integration.update",
        target=provider,
        meta={"fields": changed},
        ip=client_ip(request),
    )
    db.flush()
    fresh = integ.status(tenant_ctx(db, p))
    return next(x for x in fresh if x["provider"] == provider)


@router.post("/integrations/{provider}/test")
def test_integration(
    provider: Provider, request: Request, p: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    res = integ.test(tenant_ctx(db, p), provider)
    audit.record(
        db,
        tenant_id=p.tenant_id,
        actor_id=p.actor_id,
        action="integration.test",
        target=provider,
        meta={"ok": res["ok"], "mode": res["mode"]},
        ip=client_ip(request),
    )
    return res


# --------------------------------------------------------------------------- audit
@router.get("/audit")
def list_audit(
    action: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    p: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(AuditLog).where(AuditLog.tenant_id == p.tenant_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    return paginate(db, stmt, AuditLog, limit, cursor, audit_out)

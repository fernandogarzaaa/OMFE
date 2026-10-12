"""DB-backed job queue + transactional outbox + worker.

* `enqueue()` inserts an `outbox` row in the caller's transaction, so a job exists iff the
  business change that caused it committed. `idempotency_key` is unique per tenant: enqueuing
  the same logical job twice is a no-op.
* `claim()` takes due jobs: `FOR UPDATE SKIP LOCKED` on Postgres (many workers), an optimistic
  conditional UPDATE on SQLite. Jobs stuck in `running` past the lease are re-claimed.
* `run_job()` executes one job in its own session. On error the session is rolled back (so no
  partial side effects persist) and the failure is recorded in a *fresh* transaction:
  retry with exponential backoff, or `dead` once attempts are exhausted / the error is permanent.
* `run_worker()` is the loop behind `ofm worker`; `start_in_process_worker()` runs it on a
  daemon thread when RUN_WORKER_IN_PROCESS=true.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..adapters.http import IntegrationError
from ..adapters.meta_capi import CapiEvent
from ..adapters.tiktok import TikTokEvent
from ..db import session_factory
from ..logging_setup import redact_text
from ..models import ConversionEvent, IntegrationLog, Lead, OutboxJob, WebhookSubscription
from ..security import crypto, signing, ssrf
from ..settings import get_settings
from .context import TenantContext, load_context
from .live import record_activity

log = logging.getLogger(__name__)

LEASE_SECONDS = 300
BACKOFF_BASE = 5.0
BACKOFF_CAP = 3600.0
STATUSES = ("queued", "running", "retry", "succeeded", "dead")


class PermanentJobError(Exception):
    """Do not retry (bad payload, 4xx from provider)."""


Handler = Callable[[TenantContext, OutboxJob], dict[str, Any] | None]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


def _now() -> datetime:
    return datetime.now(UTC)


def backoff_seconds(attempts: int) -> float:
    return float(min(BACKOFF_CAP, BACKOFF_BASE * (2 ** max(0, attempts - 1))))


# --------------------------------------------------------------------------- enqueue
def enqueue(db: Session, tenant_id: uuid.UUID, kind: str, payload: dict[str, Any], idempotency_key: str,
            *, delay_seconds: float = 0, max_attempts: int = 6) -> OutboxJob | None:
    """Insert a job in the current transaction. Returns None if the idempotency key already exists."""
    if kind not in HANDLERS:
        raise ValueError(f"unknown job kind {kind!r}")
    key = idempotency_key[:128]
    existing = db.scalar(select(OutboxJob).where(OutboxJob.tenant_id == tenant_id, OutboxJob.idempotency_key == key))
    if existing is not None:
        return None
    job = OutboxJob(tenant_id=tenant_id, kind=kind, payload=payload, idempotency_key=key, status="queued",
                    max_attempts=max_attempts, run_after=_now() + timedelta(seconds=delay_seconds))
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:  # concurrent enqueue of the same key
        return None
    return job


def emit_event(db: Session, tenant_id: uuid.UUID, event_type: str, data: dict[str, Any], event_id: str) -> int:
    """Fan a domain event out to matching outbound webhook subscriptions (via the outbox)."""
    subs = db.scalars(select(WebhookSubscription).where(
        WebhookSubscription.tenant_id == tenant_id, WebhookSubscription.active.is_(True),
        WebhookSubscription.deleted_at.is_(None))).all()
    n = 0
    for sub in subs:
        events = list(sub.events or [])
        if "*" in events or event_type in events:
            if enqueue(db, tenant_id, "webhook.deliver",
                       {"subscription_id": str(sub.id), "event": event_type, "event_id": event_id, "data": data},
                       f"wh:{sub.id}:{event_type}:{event_id}"):
                n += 1
    return n


# --------------------------------------------------------------------------- claim / run
def claim(db: Session, limit: int) -> list[uuid.UUID]:
    now = _now()
    stale = now - timedelta(seconds=LEASE_SECONDS)
    due = (
        select(OutboxJob.id)
        .where(
            ((OutboxJob.status.in_(("queued", "retry"))) & (OutboxJob.run_after <= now))
            | ((OutboxJob.status == "running") & (OutboxJob.locked_at < stale))
        )
        .order_by(OutboxJob.run_after)
        .limit(limit)
    )
    if db.get_bind().dialect.name == "postgresql":  # pragma: no cover - exercised on Postgres
        ids = list(db.scalars(due.with_for_update(skip_locked=True)).all())
        if ids:
            db.execute(update(OutboxJob).where(OutboxJob.id.in_(ids))
                       .values(status="running", locked_at=now, attempts=OutboxJob.attempts + 1))
        db.commit()
        return ids
    claimed: list[uuid.UUID] = []
    for jid in db.scalars(due).all():
        job = db.get(OutboxJob, jid)
        if job is None:  # pragma: no cover
            continue
        res = db.execute(
            update(OutboxJob)
            .where(OutboxJob.id == jid, OutboxJob.status == job.status, OutboxJob.attempts == job.attempts)
            .values(status="running", locked_at=now, attempts=OutboxJob.attempts + 1)
        )
        if getattr(res, "rowcount", 0) == 1:
            claimed.append(jid)
    db.commit()
    return claimed


def run_job(job_id: uuid.UUID) -> str:
    """Execute one claimed job. Returns its new status."""
    factory = session_factory()
    db = factory()
    try:
        job = db.get(OutboxJob, job_id)
        if job is None:
            return "missing"
        fn = HANDLERS.get(job.kind)
        if fn is None:
            raise PermanentJobError(f"no handler for {job.kind}")
        ctx = load_context(db, tenant_id=job.tenant_id)
        fn(ctx, job)
        job.status = "succeeded"
        job.last_error = ""
        job.locked_at = None
        db.commit()
        return "succeeded"
    except Exception as exc:  # noqa: BLE001 - every failure is recorded, never lost
        db.rollback()
        return _record_failure(job_id, exc)
    finally:
        db.close()


def _record_failure(job_id: uuid.UUID, exc: Exception) -> str:
    permanent = isinstance(exc, PermanentJobError | ssrf.UnsafeURLError) or (
        isinstance(exc, IntegrationError) and not exc.retryable)
    msg = redact_text(f"{exc.__class__.__name__}: {exc}")[:2000]
    db = session_factory()()
    try:
        job = db.get(OutboxJob, job_id)
        if job is None:  # pragma: no cover
            return "missing"
        if permanent or job.attempts >= job.max_attempts:
            job.status = "dead"
        else:
            job.status = "retry"
            job.run_after = _now() + timedelta(seconds=backoff_seconds(job.attempts))
        job.last_error = msg
        job.locked_at = None
        db.add(IntegrationLog(tenant_id=job.tenant_id, adapter=job.kind.split(".")[0], operation=job.kind,
                              mode="n/a", ok=False, status_code=getattr(exc, "status_code", None),
                              detail={"job_id": str(job.id), "error": msg, "attempt": job.attempts}))
        if job.status == "dead":
            record_activity(db, job.tenant_id, "job_failed", {"type": "job_failed", "kind": job.kind,
                                                              "job_id": str(job.id)})
        db.commit()
        log.warning("job failed", extra={"job_id": str(job_id), "kind": job.kind, "status": job.status})
        return job.status
    finally:
        db.close()


def retry_now(db: Session, job: OutboxJob) -> None:
    job.status = "queued"
    job.run_after = _now()
    if job.attempts >= job.max_attempts:
        job.max_attempts = job.attempts + 1


def run_once(batch: int | None = None) -> int:
    db = session_factory()()
    try:
        ids = claim(db, batch or get_settings().worker_batch_size)
    finally:
        db.close()
    for jid in ids:
        run_job(jid)
    return len(ids)


def drain(max_rounds: int = 20) -> int:
    """Run due jobs until the queue is empty (tests / CLI)."""
    total = 0
    for _ in range(max_rounds):
        n = run_once()
        total += n
        if n == 0:
            break
    return total


def run_worker(stop: threading.Event | None = None, poll_interval: float | None = None) -> None:
    stop = stop or threading.Event()
    interval = poll_interval if poll_interval is not None else get_settings().worker_poll_interval
    log.info("worker started")
    while not stop.is_set():
        try:
            n = run_once()
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            n = 0
        if n == 0:
            stop.wait(interval)
    log.info("worker stopped")


def start_in_process_worker() -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    t = threading.Thread(target=run_worker, args=(stop,), daemon=True, name="ofm-worker")
    t.start()
    return t, stop


# --------------------------------------------------------------------------- handlers
def _lead(ctx: TenantContext, payload: dict[str, Any]) -> Lead:
    lead = ctx.db.scalar(select(Lead).where(Lead.tenant_id == ctx.tenant_id,
                                            Lead.id == uuid.UUID(str(payload.get("lead_id")))))
    if lead is None:
        raise PermanentJobError("lead not found")
    return lead


GHL_STAGE_ALIASES = {"contacted": ("contacted", "qualified"), "call_booked": ("call_booked", "booked")}


def ghl_stage_id(ctx: TenantContext, stage: str) -> str:
    stages = ctx.config.ghl.stages
    for key in GHL_STAGE_ALIASES.get(stage, (stage,)):
        if stages.get(key):
            return stages[key]
    return f"mock_stage_{stage}"


@handler("ghl.upsert_lead")
def _ghl_upsert(ctx: TenantContext, job: OutboxJob) -> dict[str, Any]:
    lead = _lead(ctx, job.payload)
    client = ctx.ghl()
    first, _, last = lead.name.partition(" ")
    contact = {
        "firstName": first, "lastName": last, "name": lead.name, "email": lead.email, "phone": lead.phone,
        "timezone": lead.timezone or None, "source": ctx.config.ghl.source,
        "tags": [*ctx.config.ghl.base_tags, f"tier-{lead.tier}"],
    }
    cf = ctx.config.ghl.custom_fields
    custom = [{"id": cf[k], "field_value": v} for k, v in (
        ("lead_score", lead.score), ("total_followers", lead.total_followers),
        ("earnings_range", lead.earnings_range)) if cf.get(k)]
    if custom:
        contact["customFields"] = custom
    contact_id, _new = ctx.call("ghl", "upsert_contact", client.mode, lambda: client.upsert_contact(contact))
    ctx.call("ghl", "add_tags", client.mode,
             lambda: client.add_tags(contact_id, [*ctx.config.ghl.base_tags, f"tier-{lead.tier}"]))
    from ..api.serializers import score_reasons

    note = f"OFM Engine score {lead.score}/100 ({lead.tier}).\n" + "\n".join(score_reasons(lead.score_breakdown))
    ctx.call("ghl", "add_note", client.mode, lambda: client.add_note(contact_id, note))
    if ctx.config.ghl.workflow_id:
        ctx.call("ghl", "add_to_workflow", client.mode,
                 lambda: client.add_to_workflow(contact_id, ctx.config.ghl.workflow_id))
    lead.ghl_contact_id = contact_id
    enqueue(ctx.db, ctx.tenant_id, "ghl.opportunity", {"lead_id": str(lead.id)}, f"ghl.opp:{lead.id}")
    return {"contact_id": contact_id}


@handler("ghl.opportunity")
def _ghl_opportunity(ctx: TenantContext, job: OutboxJob) -> dict[str, Any]:
    lead = _lead(ctx, job.payload)
    if not lead.ghl_contact_id:
        raise IntegrationError("lead has no GHL contact yet", status_code=None)  # retry later
    client = ctx.ghl()
    stage_id = ghl_stage_id(ctx, lead.stage)
    if lead.ghl_opportunity_id:
        status = "won" if lead.stage == "signed" else "lost" if lead.stage == "lost" else "open"
        opp_id = lead.ghl_opportunity_id
        ctx.call("ghl", "update_opportunity", client.mode,
                 lambda: client.update_opportunity_stage(opp_id, stage_id, status))
        return {"opportunity_id": opp_id}
    contact_id = lead.ghl_contact_id
    opp = ctx.call("ghl", "create_opportunity", client.mode, lambda: client.create_opportunity(
        contact_id=contact_id, pipeline_id=ctx.config.ghl.pipeline_id or "mock_pipeline",
        stage_id=stage_id, name=f"{lead.name} — creator application"))
    lead.ghl_opportunity_id = opp
    return {"opportunity_id": opp}


@handler("meta_capi.send")
def _meta_send(ctx: TenantContext, job: OutboxJob) -> dict[str, Any]:
    p = job.payload
    client = ctx.meta()
    ev = CapiEvent(
        event_name=p["event_name"], event_id=p["event_id"], event_time=int(p.get("event_time") or time.time()),
        action_source=p.get("action_source", "website"), event_source_url=p.get("event_source_url"),
        raw_user=p.get("user", {}), client_ip_address=p.get("client_ip"), client_user_agent=p.get("user_agent"),
        fbc=p.get("fbc") or None, fbp=p.get("fbp") or None, custom_data=p.get("custom_data", {}),
    )
    try:
        res = ctx.call("meta_capi", "send", client.mode, lambda: client.send([ev]),
                       {"event_name": ev.event_name, "event_id": ev.event_id})
    except ValueError as exc:  # CapiValidationError
        raise PermanentJobError(str(exc)) from exc
    row = ctx.db.scalar(select(ConversionEvent).where(
        ConversionEvent.tenant_id == ctx.tenant_id, ConversionEvent.event_id == ev.event_id,
        ConversionEvent.name == ev.event_name))
    if row is not None:
        row.capi_status = "sent"
    record_activity(ctx.db, ctx.tenant_id, "capi_sent", {
        "type": "capi_sent", "event_name": ev.event_name, "event_id": ev.event_id, "mode": client.mode})
    return res


@handler("tiktok.send")
def _tiktok_send(ctx: TenantContext, job: OutboxJob) -> dict[str, Any]:
    p = job.payload
    client = ctx.tiktok()
    ev = TikTokEvent(event=p["event_name"], event_id=p["event_id"], event_time=int(p.get("event_time") or time.time()),
                     email=p.get("user", {}).get("em"), phone=p.get("user", {}).get("ph"), ttclid=p.get("ttclid"),
                     ip=p.get("client_ip"), user_agent=p.get("user_agent"), url=p.get("event_source_url"))
    return ctx.call("tiktok", "send", client.mode, lambda: client.send([ev]), {"event_id": ev.event_id})


def _post_json(url: str, body: bytes, headers: dict[str, str]) -> int:
    with httpx.Client(timeout=get_settings().http_timeout_seconds, follow_redirects=False) as c:
        resp = c.post(url, content=body, headers=headers)
    if resp.status_code >= 400:
        raise IntegrationError(f"HTTP {resp.status_code}", status_code=resp.status_code)
    return resp.status_code


@handler("webhook.deliver")
def _webhook_deliver(ctx: TenantContext, job: OutboxJob) -> dict[str, Any]:
    p = job.payload
    sub = ctx.db.scalar(select(WebhookSubscription).where(
        WebhookSubscription.tenant_id == ctx.tenant_id, WebhookSubscription.id == uuid.UUID(p["subscription_id"])))
    if sub is None or sub.deleted_at is not None or not sub.active:
        return {"skipped": "subscription removed"}
    secret = crypto.decrypt_str(sub.secret_enc, tenant_id=str(ctx.tenant_id), name=f"webhook:{sub.id}")
    body = json.dumps({"id": str(job.id), "event": p["event"], "event_id": p["event_id"], "tenant": ctx.slug,
                       "data": p.get("data", {})}, separators=(",", ":"), sort_keys=True).encode()
    headers = {
        "Content-Type": "application/json", signing.SIGNATURE_HEADER: signing.sign(secret, body),
        "X-OFM-Event": p["event"], "X-OFM-Delivery": str(job.id), "User-Agent": "OFM-Engine-Webhooks/1.0",
    }
    s = get_settings()
    mode = "live" if s.outbound_webhooks_live else "mock"
    if mode == "live":
        ssrf.check_webhook_url(sub.url)  # re-checked at send time (DNS rebinding)
    status = ctx.call("webhook", "deliver", mode,
                      lambda: _post_json(sub.url, body, headers) if mode == "live" else 200,
                      {"event": p["event"], "subscription_id": str(sub.id)})
    return {"status": status, "mode": mode}


@handler("alert.send")
def _alert(ctx: TenantContext, job: OutboxJob) -> dict[str, Any]:
    text = str(job.payload.get("text", ""))[:1800]
    sent = []
    for name, payload in (("SLACK_WEBHOOK_URL", {"text": text}), ("DISCORD_WEBHOOK_URL", {"content": text})):
        url = ctx.secret(name)
        mode = "live" if url else "mock"
        if url:
            ssrf.check_integration_url(url)
            data = json.dumps(payload).encode()
            ctx.call("alert", name.split("_")[0].lower(), mode,
                     lambda u=url, d=data: _post_json(u, d, {"Content-Type": "application/json"}))  # type: ignore[misc]
        sent.append({"channel": name, "mode": mode})
    return {"sent": sent}

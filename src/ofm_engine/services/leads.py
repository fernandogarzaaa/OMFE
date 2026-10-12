"""Recruitment leads: public application intake, scoring, stage changes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ..hashing import keyed_hash, normalize_email, sha256
from ..models import ConversionEvent, Lead
from ..scoring import score_lead
from ..settings import get_settings
from . import jobs
from .context import TenantContext
from .kpis import invalidate
from .live import record_activity

MAX_FOLLOWERS = 1_000_000_000


@dataclass
class Application:
    name: str
    email: str
    phone: str | None
    socials: dict[str, str]
    followers: dict[str, int]
    earnings_range: str
    timezone: str
    age_confirmed: bool
    consent: bool
    variant: str = ""
    event_id: str = ""
    attribution: dict[str, Any] | None = None
    client_ip: str = ""
    user_agent: str = ""
    source_url: str = ""


def _score(ctx: TenantContext, lead: Lead) -> None:
    cfg = ctx.config.scoring or {}
    res = score_lead(
        followers={k: int(v) for k, v in (lead.followers or {}).items()},
        earnings_range=lead.earnings_range,
        has_phone=bool(lead.phone),
        socials=dict(lead.socials or {}),
        timezone=lead.timezone,
        target_timezones=ctx.config.target_timezones,
        weights=cfg.get("weights"),
        tiers=cfg.get("tiers"),
        ai_adjustment=int((lead.ai_enrichment or {}).get("adjustment", 0)),
    )
    lead.score, lead.tier, lead.score_breakdown = res.score, res.tier, res.breakdown


def submit_application(ctx: TenantContext, app: Application) -> tuple[Lead, bool]:
    """Create (or idempotently return) a lead and enqueue CRM/CAPI/webhook side effects in the
    same transaction. Returns (lead, created)."""
    if not (app.age_confirmed and app.consent):
        raise ValueError("age_confirmed and consent are required")
    db = ctx.db
    email_n = normalize_email(app.email)
    idem = sha256(f"{ctx.tenant_id}:{email_n}")[:64]
    existing = db.scalar(select(Lead).where(Lead.tenant_id == ctx.tenant_id, Lead.idempotency_key == idem))
    if existing is not None:
        return existing, False
    followers = {k[:32]: max(0, min(int(v or 0), MAX_FOLLOWERS)) for k, v in app.followers.items()}
    event_id = app.event_id[:64] if app.event_id else uuid.uuid4().hex
    attribution = {k: str(v)[:300] for k, v in (app.attribution or {}).items()}
    lead = Lead(
        tenant_id=ctx.tenant_id,
        idempotency_key=idem,
        name=app.name.strip()[:200],
        email=email_n,
        phone=(app.phone or "").strip()[:32] or None,
        email_hash=keyed_hash(email_n, get_settings().pii_hash_salt.get_secret_value()),
        socials={k[:32]: str(v)[:200] for k, v in app.socials.items() if v},
        followers=followers,
        total_followers=sum(followers.values()),
        earnings_range=app.earnings_range[:32],
        timezone=app.timezone[:64],
        age_confirmed=True,
        consent=True,
        consent_version=ctx.config.funnel.consent_version,
        consent_at=datetime.now(UTC),
        variant=app.variant[:32],
        attribution=attribution,
        event_id=event_id,
        stage="new",
    )
    _score(ctx, lead)
    db.add(lead)
    db.flush()
    db.add(
        ConversionEvent(
            tenant_id=ctx.tenant_id,
            name="Lead",
            event_id=event_id,
            lead_id=lead.id,
            variant=lead.variant,
            source="server",
        )
    )
    f = ctx.config.features
    if f.ghl_sync:
        jobs.enqueue(db, ctx.tenant_id, "ghl.upsert_lead", {"lead_id": str(lead.id)}, f"ghl.upsert:{lead.id}")
    capi_payload = {
        "event_name": "Lead",
        "event_id": event_id,
        "event_time": int(datetime.now(UTC).timestamp()),
        "event_source_url": app.source_url or f"{get_settings().public_base_url}/apply/{ctx.slug}",
        "user": {"em": email_n, "ph": lead.phone, "fn": app.name.split(" ")[0], "external_id": str(lead.id)},
        "client_ip": app.client_ip or None,
        "user_agent": app.user_agent or "unknown",
        "fbc": attribution.get("fbc", ""),
        "fbp": attribution.get("fbp", ""),
        "ttclid": attribution.get("ttclid", ""),
        "custom_data": {"lead_tier": lead.tier, "variant": lead.variant},
    }
    if f.meta_capi:
        jobs.enqueue(db, ctx.tenant_id, "meta_capi.send", capi_payload, f"capi:Lead:{event_id}")
    if f.tiktok_events:
        jobs.enqueue(db, ctx.tenant_id, "tiktok.send", capi_payload, f"tiktok:Lead:{event_id}")
    if f.alerts and lead.tier in ("hot", "warm"):
        jobs.enqueue(
            db,
            ctx.tenant_id,
            "alert.send",
            {"text": f"New {lead.tier} creator lead: {lead.name} — score {lead.score}/100"},
            f"alert:lead:{lead.id}",
        )
    if f.outbound_webhooks:
        jobs.emit_event(db, ctx.tenant_id, "lead.created", lead_event_data(lead), event_id)
    record_activity(
        db,
        ctx.tenant_id,
        "lead",
        {
            "type": "lead",
            "lead_id": str(lead.id),
            "score": lead.score,
            "tier": lead.tier,
            "variant": lead.variant,
        },
    )
    invalidate(ctx.tenant_id)
    return lead, True


def lead_event_data(lead: Lead) -> dict[str, Any]:
    return {
        "lead_id": str(lead.id),
        "name": lead.name,
        "score": lead.score,
        "tier": lead.tier,
        "stage": lead.stage,
        "followers": lead.total_followers,
        "earnings_range": lead.earnings_range,
        "variant": lead.variant,
    }


def rescore(ctx: TenantContext, lead: Lead) -> Lead:
    _score(ctx, lead)
    return lead


def change_stage(ctx: TenantContext, lead: Lead, stage: str) -> None:
    if stage == lead.stage:
        return
    old = lead.stage
    lead.stage = stage
    db = ctx.db
    stamp = uuid.uuid4().hex
    if ctx.config.features.ghl_sync:
        jobs.enqueue(
            db, ctx.tenant_id, "ghl.opportunity", {"lead_id": str(lead.id)}, f"ghl.opp:{lead.id}:{stamp}"
        )
    if ctx.config.features.outbound_webhooks:
        jobs.emit_event(
            db, ctx.tenant_id, "lead.stage_changed", {**lead_event_data(lead), "from": old}, stamp
        )
    if stage == "signed":
        db.add(
            ConversionEvent(
                tenant_id=ctx.tenant_id,
                name="CompleteRegistration",
                event_id=lead.event_id,
                lead_id=lead.id,
                source="server",
            )
        )
    record_activity(
        db,
        ctx.tenant_id,
        "lead_stage",
        {"type": "lead", "lead_id": str(lead.id), "stage": stage, "from": old},
    )
    invalidate(ctx.tenant_id)

"""Model -> contract JSON shapes (docs/api-contract.md)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..models import ApiKey, AuditLog, Creator, Lead, OutboxJob, RevenueRecord, Tenant, TrackingLink, User

LEAD_STAGES: list[tuple[str, str]] = [
    ("new", "New"), ("contacted", "Contacted"), ("call_booked", "Call booked"), ("signed", "Signed"), ("lost", "Lost"),
]
STAGE_KEYS = [k for k, _ in LEAD_STAGES]

_REASON_LABELS = {
    "audience": "audience size", "multi_platform": "multi-platform presence", "earnings": "current earnings",
    "contactable": "phone provided", "socials": "socials linked", "timezone": "target timezone",
    "ai_adjustment": "AI review",
}


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def user_out(u: User, tenant: Tenant | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(u.id), "email": u.email, "name": u.name, "role": u.role, "totp_enabled": u.totp_enabled,
        "is_active": u.is_active, "created_at": iso(u.created_at), "last_login_at": iso(u.last_login_at),
    }
    if tenant is not None:
        out["tenant"] = {"id": str(tenant.id), "name": tenant.name, "slug": tenant.slug}
    return out


def creator_out(c: Creator) -> dict[str, Any]:
    return {
        "id": str(c.id), "name": c.display_name, "handle": c.slug, "status": c.status,
        "channels": list(c.channels or []), "bio": c.bio, "avatar_url": c.avatar_url,
        "bio_links": list(c.bio_links or []), "created_at": iso(c.created_at),
    }


def score_reasons(breakdown: dict[str, Any]) -> list[str]:
    out = []
    for key, pts in sorted(breakdown.items(), key=lambda kv: -float(kv[1] or 0)):
        if pts:
            out.append(f"{'+' if float(pts) > 0 else ''}{float(pts):g} {_REASON_LABELS.get(key, key)}")
    return out


def lead_out(lead: Lead) -> dict[str, Any]:
    return {
        "id": str(lead.id), "name": lead.name, "email": lead.email, "phone": lead.phone,
        "socials": dict(lead.socials or {}), "followers": int(lead.total_followers or 0),
        "followers_by_platform": dict(lead.followers or {}), "earnings_range": lead.earnings_range,
        "timezone": lead.timezone, "score": lead.score, "tier": lead.tier,
        "score_reasons": score_reasons(lead.score_breakdown or {}), "stage": lead.stage,
        "variant": lead.variant, "attribution": dict(lead.attribution or {}),
        "ghl_contact_id": lead.ghl_contact_id, "created_at": iso(lead.created_at),
    }


def link_out(link: TrackingLink, clicks: int = 0) -> dict[str, Any]:
    return {
        "id": str(link.id), "slug": link.slug, "creator_id": str(link.creator_id) if link.creator_id else None,
        "channel": link.channel, "destination_url": link.destination_url, "campaign": link.campaign,
        "capi_on_click": link.capi_on_click, "active": link.active, "clicks": int(clicks),
        "created_at": iso(link.created_at),
    }


def revenue_out(r: RevenueRecord) -> dict[str, Any]:
    return {
        "id": str(r.id), "creator_id": str(r.creator_id) if r.creator_id else None, "date": r.day.isoformat(),
        "campaign": r.campaign, "gross_cents": int(round(r.gross * 100)), "net_cents": int(round(r.net * 100)),
        "revenue_cents": int(round(r.gross * 100)), "new_subs": r.new_subs, "source_file": r.source_file,
        "created_at": iso(r.created_at),
    }


def job_out(j: OutboxJob) -> dict[str, Any]:
    return {
        "id": str(j.id), "kind": j.kind, "status": j.status, "attempts": j.attempts, "max_attempts": j.max_attempts,
        "last_error": j.last_error, "run_after": iso(j.run_after), "created_at": iso(j.created_at),
        "updated_at": iso(j.updated_at),
    }


def api_key_out(k: ApiKey) -> dict[str, Any]:
    return {
        "id": str(k.id), "name": k.name, "prefix": k.prefix, "role": k.role, "created_at": iso(k.created_at),
        "last_used_at": iso(k.last_used_at), "revoked": k.revoked_at is not None,
    }


def audit_out(a: AuditLog) -> dict[str, Any]:
    return {
        "id": str(a.id), "actor_id": str(a.actor_id) if a.actor_id else None, "actor_type": a.actor_type,
        "action": a.action, "target": a.target, "meta": a.meta, "created_at": iso(a.created_at),
    }

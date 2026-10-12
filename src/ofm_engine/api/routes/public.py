"""Public, unauthenticated surface: application funnel, link-in-bio, tracking redirect, inbound hooks."""

from __future__ import annotations

import json
import secrets
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...attribution import append_params, build_fbp
from ...db import get_db
from ...models import Creator, FunnelVariant, InboundWebhook, Lead
from ...security import signing
from ...services import leads as lead_svc
from ...services.context import TenantContext, TenantNotFoundError, load_context
from ...services.tracking import find_link, record_click
from ...settings import get_settings
from ...tenant_config import SLUG_RE
from ..deps import client_ip
from ..errors import ApiError, not_found

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent.parent / "templates"))
api_router = APIRouter(tags=["public"])
page_router = APIRouter(tags=["pages"], include_in_schema=False)


def _ctx(db: Session, slug: str) -> TenantContext:
    if not SLUG_RE.fullmatch(slug or ""):
        raise not_found("tenant")
    try:
        return load_context(db, slug=slug)
    except TenantNotFoundError as exc:
        raise not_found("tenant") from exc


# --------------------------------------------------------------------------- apply API
class ApplyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    socials: dict[str, str] = Field(default_factory=dict, max_length=10)
    followers: dict[str, int] | int = 0
    earnings_range: str = Field(default="", max_length=32)
    timezone: str = Field(default="", max_length=64)
    age_confirmed: bool = False
    consent: bool = False
    variant: str = Field(default="", max_length=32)
    event_id: str = Field(default="", max_length=64)
    attribution: dict[str, str] = Field(default_factory=dict, max_length=20)
    website: str = Field(default="", max_length=200)  # honeypot: humans never fill it

    @field_validator("followers")
    @classmethod
    def _followers(cls, v: dict[str, int] | int) -> dict[str, int] | int:
        vals = v.values() if isinstance(v, dict) else [v]
        if any(int(x) < 0 for x in vals):
            raise ValueError("followers must be >= 0")
        return v


def _apply(db: Session, request: Request, slug: str, body: ApplyIn) -> tuple[Lead, bool, TenantContext]:
    ctx = _ctx(db, slug)
    if not body.age_confirmed or not body.consent:
        raise ApiError(
            422,
            "age_and_consent_required",
            "applicants must confirm they are 18+ and consent to be contacted",
        )
    if body.earnings_range and body.earnings_range not in ctx.config.funnel.earnings_ranges:
        raise ApiError(422, "validation_error", "earnings_range: not an allowed value")
    followers = body.followers if isinstance(body.followers, dict) else {"total": int(body.followers)}
    attribution = dict(body.attribution)
    for c in ("_fbc", "_fbp"):
        if request.cookies.get(c) and not attribution.get(c[1:]):
            attribution[c[1:]] = request.cookies[c]
    lead, created = lead_svc.submit_application(
        ctx,
        lead_svc.Application(
            name=body.name,
            email=str(body.email),
            phone=body.phone,
            socials=body.socials,
            followers=followers,
            earnings_range=body.earnings_range,
            timezone=body.timezone,
            age_confirmed=body.age_confirmed,
            consent=body.consent,
            variant=body.variant,
            event_id=body.event_id,
            attribution=attribution,
            client_ip=client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:400],
            source_url=f"{get_settings().public_base_url}/apply/{slug}",
        ),
    )
    return lead, created, ctx


@api_router.post("/public/{tenant_slug}/apply", status_code=201)
def apply(tenant_slug: str, body: ApplyIn, request: Request, db: Session = Depends(get_db)) -> Any:
    if body.website:  # bot: pretend success, store nothing
        return {"ok": True, "id": str(uuid.uuid4()), "event_id": body.event_id or uuid.uuid4().hex}
    lead, created, ctx = _apply(db, request, tenant_slug, body)
    out = {
        "ok": True,
        "id": str(lead.id),
        "event_id": lead.event_id,
        "duplicate": not created,
        "thank_you": ctx.config.funnel.thank_you,
        "booking_url": ctx.config.funnel.booking_url,
    }
    return out if created else JSONResponse(out, status_code=200)


# --------------------------------------------------------------------------- inbound signed webhooks
@api_router.post("/hooks/inbound/{tenant_slug}")
async def inbound_hook(tenant_slug: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Signed callbacks from n8n / Make / Zapier (X-OFM-Signature, HMAC-SHA256, 5-min window, replay-deduped).
    Supported: {"type": "lead.stage", "lead_id": ..., "stage": ...}."""
    ctx = _ctx(db, tenant_slug)
    body = await request.body()
    if len(body) > 64 * 1024:
        raise ApiError(413, "payload_too_large", "max 64 KB")
    secret = ctx.secret("INBOUND_HMAC_SECRET")
    header = request.headers.get(signing.SIGNATURE_HEADER)
    if not secret:
        raise ApiError(403, "inbound_disabled", "INBOUND_HMAC_SECRET is not configured for this tenant")
    try:
        signing.verify(secret, body, header, tolerance=get_settings().webhook_tolerance_seconds)
    except signing.SignatureError as exc:
        raise ApiError(401, "invalid_signature", str(exc)) from exc
    assert header is not None
    try:
        payload = json.loads(body or b"{}")
    except ValueError as exc:
        raise ApiError(422, "validation_error", "body must be JSON") from exc
    dedupe = signing.parse_header(header)[1][0][:128]
    try:
        with db.begin_nested():
            db.add(
                InboundWebhook(
                    tenant_id=ctx.tenant_id,
                    source="inbound",
                    dedupe_key=dedupe,
                    event_type=str(payload.get("type", ""))[:64],
                    verified=True,
                )
            )
            db.flush()
    except IntegrityError as exc:
        raise ApiError(409, "replayed", "this delivery was already processed") from exc
    if payload.get("type") == "lead.stage":
        try:
            lead_id = uuid.UUID(str(payload.get("lead_id")))
        except ValueError as exc:
            raise ApiError(422, "validation_error", "lead_id invalid") from exc
        lead = db.scalar(select(Lead).where(Lead.tenant_id == ctx.tenant_id, Lead.id == lead_id))
        stage = str(payload.get("stage"))
        if lead is None or stage not in ("new", "contacted", "call_booked", "signed", "lost"):
            raise ApiError(422, "validation_error", "unknown lead or stage")
        lead_svc.change_stage(ctx, lead, stage)
    return {"ok": True}


# --------------------------------------------------------------------------- pages
def _variants(ctx: TenantContext, db: Session) -> list[dict[str, str]]:
    rows = db.scalars(
        select(FunnelVariant)
        .where(FunnelVariant.tenant_id == ctx.tenant_id, FunnelVariant.active.is_(True))
        .order_by(FunnelVariant.key)
    ).all()
    if rows:
        return [{"key": r.key, "headline": r.headline, "subhead": r.subhead} for r in rows]
    return [v.model_dump() for v in ctx.config.funnel.variants]


def _nonce(request: Request) -> str:
    return getattr(request.state, "csp_nonce", "")


@page_router.get("/apply/{tenant_slug}", response_class=HTMLResponse)
def funnel_page(
    tenant_slug: str, request: Request, v: str | None = None, db: Session = Depends(get_db)
) -> Response:
    ctx = _ctx(db, tenant_slug)
    variants = _variants(ctx, db) if ctx.config.features.ab_testing else _variants(ctx, db)[:1]
    keys = [x["key"] for x in variants]
    cookie_name = f"ofm_v_{tenant_slug.replace('-', '_')}"
    chosen = v if v in keys else request.cookies.get(cookie_name)
    if chosen not in keys:
        chosen = keys[secrets.randbelow(len(keys))]
    variant = next(x for x in variants if x["key"] == chosen)
    event_id = uuid.uuid4().hex
    resp = TEMPLATES.TemplateResponse(
        request,
        "apply.html",
        {
            "cfg": ctx.config,
            "variant": variant,
            "slug": tenant_slug,
            "event_id": event_id,
            "nonce": _nonce(request),
            "query": dict(request.query_params),
        },
    )
    resp.set_cookie(
        cookie_name,
        chosen,
        max_age=60 * 60 * 24 * 30,
        samesite="lax",
        httponly=True,
        secure=get_settings().secure_cookies,
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@page_router.get("/c/{tenant_slug}/{creator_handle}", response_class=HTMLResponse)
def link_in_bio(
    tenant_slug: str, creator_handle: str, request: Request, db: Session = Depends(get_db)
) -> Response:
    ctx = _ctx(db, tenant_slug)
    creator = db.scalar(
        select(Creator).where(
            Creator.tenant_id == ctx.tenant_id,
            Creator.slug == creator_handle.lower()[:64],
            Creator.deleted_at.is_(None),
            Creator.status != "paused",
        )
    )
    if creator is None:
        raise not_found("creator")
    return TEMPLATES.TemplateResponse(
        request,
        "bio.html",
        {
            "cfg": ctx.config,
            "creator": creator,
            "nonce": _nonce(request),
            "event_id": uuid.uuid4().hex,
        },
    )


@page_router.get("/l/{slug}")
def redirect(slug: str, request: Request, db: Session = Depends(get_db)) -> Response:
    link = find_link(db, slug[:64])
    if link is None:
        raise not_found("link")
    ctx, event_id = record_click(
        db,
        link,
        query=dict(request.query_params),
        cookies=dict(request.cookies),
        user_agent=request.headers.get("user-agent", ""),
        referrer=request.headers.get("referer"),
        ip=client_ip(request),
    )
    dest = append_params(
        link.destination_url,
        {
            k: str(v)
            for k, v in {
                "utm_source": ctx.utm_source or link.channel,
                "utm_medium": ctx.utm_medium,
                "utm_campaign": ctx.utm_campaign or link.campaign,
                "utm_content": ctx.utm_content,
            }.items()
        },
    )
    tctx = load_context(db, tenant_id=link.tenant_id)
    pixel_id = tctx.config.meta.pixel_id if link.capi_on_click and tctx.config.features.meta_capi else ""
    resp: Response
    if link.capi_on_click and not ctx.is_bot:
        resp = TEMPLATES.TemplateResponse(
            request,
            "redirect.html",
            {"dest": dest, "pixel_id": pixel_id, "event_id": event_id, "nonce": _nonce(request)},
        )
    else:
        resp = RedirectResponse(dest, status_code=302)
    s = get_settings()
    if not request.cookies.get("_fbp") and not ctx.is_bot:
        resp.set_cookie(
            "_fbp", ctx.fbp or build_fbp(), max_age=60 * 60 * 24 * 90, samesite="lax", secure=s.secure_cookies
        )
    if ctx.fbc:
        resp.set_cookie("_fbc", ctx.fbc, max_age=60 * 60 * 24 * 90, samesite="lax", secure=s.secure_cookies)
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["X-OFM-Event-Id"] = event_id
    return resp


@page_router.post("/apply/{tenant_slug}", response_class=HTMLResponse)
async def funnel_form_post(tenant_slug: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """No-JS fallback for the funnel form (same validation as the JSON endpoint)."""
    form = await request.form()
    ctx = _ctx(db, tenant_slug)

    def g(k: str) -> str:
        v = form.get(k)
        return v.strip() if isinstance(v, str) else ""

    socials = {p: g(p) for p in ("instagram", "tiktok") if g(p)}
    followers = {
        p: int(g(f"{p}_followers")) for p in ("instagram", "tiktok") if g(f"{p}_followers").isdigit()
    }
    attribution = {k[5:]: g(k) for k in form if isinstance(k, str) and k.startswith("attr_") and g(k)}
    try:
        body = ApplyIn(
            name=g("name"),
            email=g("email"),
            phone=g("phone") or None,
            socials=socials,
            followers=followers,
            earnings_range=g("earnings_range"),
            timezone=g("timezone"),
            age_confirmed=g("age_confirmed") == "true",
            consent=g("consent") == "true",
            variant=g("variant"),
            event_id=g("event_id"),
            attribution=attribution,
            website=g("website"),
        )
        if not body.website:
            _apply(db, request, tenant_slug, body)
    except ApiError as exc:
        return TEMPLATES.TemplateResponse(
            request,
            "thanks.html",
            {"cfg": ctx.config, "slug": tenant_slug, "error": exc.message},
            status_code=exc.status,
        )
    except ValueError:
        return TEMPLATES.TemplateResponse(
            request,
            "thanks.html",
            {"cfg": ctx.config, "slug": tenant_slug, "error": "Please check your name and email."},
            status_code=422,
        )
    return TEMPLATES.TemplateResponse(
        request, "thanks.html", {"cfg": ctx.config, "slug": tenant_slug, "error": None}
    )

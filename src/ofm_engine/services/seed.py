"""Demo data: tenant, users, creators, links, clicks, leads, revenue (deterministic-ish, mock-only)."""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..hashing import sha256
from ..models import Click, ClickRollupDaily, Creator, Lead, RevenueRecord, Tenant, TrackingLink, User
from ..scoring import score_lead
from ..security.passwords import hash_password
from ..settings import get_settings
from ..tenant_config import build_config
from .live import record_activity

DEMO_SLUG = "demo"
CHANNELS = ("instagram", "tiktok", "reddit", "x")


@dataclass
class SeedResult:
    tenant: Tenant
    passwords: dict[str, str]
    created: bool


def ensure_tenant(db: Session, slug: str, name: str) -> tuple[Tenant, bool]:
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if t is not None:
        return t, False
    t = Tenant(slug=slug, name=name)
    db.add(t)
    db.flush()
    return t, True


def _user(db: Session, tenant: Tenant, email: str, role: str, name: str, password: str | None) -> str | None:
    u = db.scalar(select(User).where(User.email == email))
    if u is not None:
        if password:
            u.password_hash = hash_password(password)
            u.failed_logins, u.locked_until = 0, None
            return password
        return None
    pw = password or secrets.token_urlsafe(12)
    db.add(User(tenant_id=tenant.id, email=email, name=name, role=role, password_hash=hash_password(pw)))
    return pw


def seed_demo(db: Session, *, slug: str = DEMO_SLUG, admin_password: str | None = None,
              member_password: str | None = None, days: int = 30, rng_seed: int = 7) -> SeedResult:
    cfg = build_config(slug, "Demo Agency", get_settings().clients_dir, None)
    tenant, created = ensure_tenant(db, slug, cfg.name)
    passwords: dict[str, str] = {}
    for email, role, name, pw in (("admin@example.com", "admin", "Demo Admin", admin_password),
                                  ("member@example.com", "member", "Demo Member", member_password)):
        got = _user(db, tenant, email, role, name, pw)
        if got:
            passwords[email] = got
    if not created and db.scalar(select(Creator.id).where(Creator.tenant_id == tenant.id).limit(1)):
        return SeedResult(tenant, passwords, False)
    rng = random.Random(rng_seed)  # noqa: S311 - demo data
    seeds = cfg.creators or []
    creators: list[Creator] = []
    for i, cs in enumerate(seeds or [None, None, None]):  # type: ignore[list-item]
        if cs is None:
            handle, name, bio, links = f"creator{i + 1}", f"Creator {i + 1}", "Lifestyle & fitness", []
        else:
            handle, name, bio, links = cs.slug, cs.display_name, cs.bio, cs.links
        c = Creator(tenant_id=tenant.id, slug=handle, display_name=name, bio=bio, status="active",
                    channels=list(CHANNELS[: 2 + i % 3]), bio_links=links)
        db.add(c)
        creators.append(c)
    db.flush()
    links_: list[TrackingLink] = []
    for c in creators:
        for ch in c.channels:
            lk = TrackingLink(tenant_id=tenant.id, creator_id=c.id, slug=f"{c.slug}-{ch}"[:64], channel=ch,
                              campaign=f"{c.slug}_{ch}", destination_url="https://onlyfans.com/" + c.slug)
            db.add(lk)
            links_.append(lk)
    rec = TrackingLink(tenant_id=tenant.id, slug=f"{slug}-recruit", channel="instagram", campaign="recruit_ig",
                       destination_url=f"{get_settings().public_base_url}/apply/{slug}", capi_on_click=True)
    db.add(rec)
    links_.append(rec)
    db.flush()
    today = datetime.now(UTC).date()
    for d in range(days):
        day = today - timedelta(days=d)
        for lk in links_:
            n = rng.randint(5, 60)
            db.add(ClickRollupDaily(tenant_id=tenant.id, link_id=lk.id, day=day, clicks=n, bot_clicks=n // 20))
        for c in creators:
            gross = round(rng.uniform(80, 900), 2)
            db.add(RevenueRecord(tenant_id=tenant.id, creator_id=c.id, day=day, campaign=f"{c.slug}_{c.channels[0]}",
                                 gross=gross, net=round(gross * 0.8, 2), new_subs=rng.randint(0, 25),
                                 source_file="seed", row_hash=sha256(f"seed|{tenant.id}|{c.id}|{day}")))
    first = ["Ava", "Mia", "Luna", "Zoe", "Ivy", "Nova", "Ella", "Ruby", "Skye", "Jade", "Lily", "Rose"]
    stages = ["new", "new", "new", "contacted", "contacted", "call_booked", "signed", "lost"]
    for i in range(24):
        followers = {"instagram": rng.randint(500, 400_000), "tiktok": rng.randint(0, 900_000)}
        er = rng.choice(["none", "0-1k", "1k-5k", "5k-10k", "10k-25k"])
        res = score_lead(followers=followers, earnings_range=er, has_phone=i % 3 != 0,
                         socials={"instagram": "@x"}, timezone="America/New_York",
                         target_timezones=cfg.target_timezones)
        email = f"{first[i % 12].lower()}{i}@example.net"
        lead = Lead(
            tenant_id=tenant.id, idempotency_key=sha256(f"{tenant.id}:{email}")[:64], name=f"{first[i % 12]} Demo{i}",
            email=email, phone="+15555550" + str(100 + i) if i % 3 else None, socials={"instagram": f"@{first[i % 12].lower()}{i}"},
            followers=followers, total_followers=sum(followers.values()), earnings_range=er, timezone="America/New_York",
            age_confirmed=True, consent=True, consent_version=cfg.funnel.consent_version, variant="A" if i % 2 else "B",
            attribution={"utm_source": rng.choice(CHANNELS)}, score=res.score, tier=res.tier,
            score_breakdown=res.breakdown, stage=stages[i % len(stages)], event_id=secrets.token_hex(16),
        )
        lead.created_at = datetime.now(UTC) - timedelta(days=rng.randint(0, days - 1), hours=rng.randint(0, 23))
        db.add(lead)
    db.flush()
    return SeedResult(tenant, passwords, True)


def synthetic_event(db: Session, tenant: Tenant, rng: random.Random) -> dict[str, Any]:
    """Emit one fake live event (click / lead / capi / revenue) for `seed-demo --stream`."""
    kind = rng.choices(["click", "lead", "capi_sent", "revenue_import"], weights=[70, 12, 12, 6])[0]
    link = db.scalar(select(TrackingLink).where(TrackingLink.tenant_id == tenant.id).limit(1)
                     .offset(rng.randint(0, 5)))
    if kind == "click" and link is not None:
        db.add(Click(tenant_id=tenant.id, link_id=link.id, utm_source=link.channel, device="mobile"))
        day = datetime.now(UTC).date()
        roll = db.scalar(select(ClickRollupDaily).where(ClickRollupDaily.link_id == link.id, ClickRollupDaily.day == day))
        if roll is None:
            db.add(ClickRollupDaily(tenant_id=tenant.id, link_id=link.id, day=day, clicks=1))
        else:
            roll.clicks += 1
        data: dict[str, Any] = {"type": "click", "slug": link.slug, "channel": link.channel, "synthetic": True}
    elif kind == "lead":
        data = {"type": "lead", "score": rng.randint(20, 95), "synthetic": True}
    elif kind == "capi_sent":
        data = {"type": "capi_sent", "event_name": "Lead", "mode": "mock", "synthetic": True}
    else:
        data = {"type": "revenue_import", "imported": 1, "revenue_cents": rng.randint(1000, 50000),
                "subs": rng.randint(0, 5), "synthetic": True}
    record_activity(db, tenant.id, kind, data)
    return data

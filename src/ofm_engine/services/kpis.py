"""KPI rollups for the dashboard (cached per tenant/range, invalidated on writes)."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ClickRollupDaily, Creator, Lead, RevenueRecord, TrackingLink

RANGES = {"7d": 7, "30d": 30, "90d": 90}
CACHE_TTL = 30.0
_cache: dict[tuple[str, str], tuple[float, int, dict[str, Any]]] = {}
_gen: dict[str, int] = {}
_lock = threading.Lock()


def invalidate(tenant_id: uuid.UUID) -> None:
    with _lock:
        _gen[str(tenant_id)] = _gen.get(str(tenant_id), 0) + 1


def get_kpis(db: Session, tenant_id: uuid.UUID, range_key: str = "30d", today: date | None = None) -> dict[str, Any]:
    key = (str(tenant_id), range_key)
    gen = _gen.get(str(tenant_id), 0)
    hit = _cache.get(key)
    if today is None and hit and hit[1] == gen and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[2]
    data = compute(db, tenant_id, range_key, today)
    if today is None:
        with _lock:
            _cache[key] = (time.monotonic(), gen, data)
    return data


def _ratio(a: int, b: int) -> float:
    return round(a / b, 4) if b else 0.0


def compute(db: Session, tenant_id: uuid.UUID, range_key: str, today: date | None = None) -> dict[str, Any]:
    days = RANGES[range_key]
    end = today or datetime.now(UTC).date()
    start = end - timedelta(days=days - 1)
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    series: dict[date, dict[str, int]] = {
        start + timedelta(days=i): {"clicks": 0, "leads": 0, "revenue_cents": 0} for i in range(days)}

    link_rows = db.execute(
        select(ClickRollupDaily.day, TrackingLink.channel, TrackingLink.campaign, TrackingLink.creator_id,
               func.sum(ClickRollupDaily.clicks - ClickRollupDaily.bot_clicks))
        .join(TrackingLink, TrackingLink.id == ClickRollupDaily.link_id)
        .where(ClickRollupDaily.tenant_id == tenant_id, TrackingLink.tenant_id == tenant_id,
               ClickRollupDaily.day >= start, ClickRollupDaily.day <= end)
        .group_by(ClickRollupDaily.day, TrackingLink.channel, TrackingLink.campaign, TrackingLink.creator_id)
    ).all()
    by_channel: dict[str, dict[str, Any]] = {}
    campaign_channel: dict[str, str] = {}
    clicks = 0
    for day, channel, campaign, _creator, n in link_rows:
        n = int(n or 0)
        clicks += n
        if day in series:
            series[day]["clicks"] += n
        by_channel.setdefault(channel, {"channel": channel, "clicks": 0, "leads": 0, "revenue_cents": 0})
        by_channel[channel]["clicks"] += n
        campaign_channel.setdefault(campaign, channel)
    for campaign, channel in db.execute(select(TrackingLink.campaign, TrackingLink.channel)
                                        .where(TrackingLink.tenant_id == tenant_id)).all():
        campaign_channel.setdefault(campaign, channel)

    leads = db.execute(
        select(Lead.created_at, Lead.stage, Lead.attribution)
        .where(Lead.tenant_id == tenant_id, Lead.deleted_at.is_(None), Lead.created_at >= start_dt)
    ).all()
    n_leads = len(leads)
    signed = 0
    for created, stage, attribution in leads:
        d = created.date()
        if d in series:
            series[d]["leads"] += 1
        if stage == "signed":
            signed += 1
        ch = (attribution or {}).get("channel") or (attribution or {}).get("utm_source")
        if ch:
            by_channel.setdefault(ch, {"channel": ch, "clicks": 0, "leads": 0, "revenue_cents": 0})
            by_channel[ch]["leads"] += 1

    rev_rows = db.execute(
        select(RevenueRecord.day, RevenueRecord.campaign, RevenueRecord.creator_id,
               func.sum(RevenueRecord.gross), func.sum(RevenueRecord.new_subs))
        .where(RevenueRecord.tenant_id == tenant_id, RevenueRecord.day >= start, RevenueRecord.day <= end)
        .group_by(RevenueRecord.day, RevenueRecord.campaign, RevenueRecord.creator_id)
    ).all()
    revenue = 0
    subs = 0
    by_creator: dict[str, dict[str, Any]] = {}
    for day, campaign, creator_id, gross, new_subs in rev_rows:
        cents = int(round(float(gross or 0) * 100))
        revenue += cents
        subs += int(new_subs or 0)
        if day in series:
            series[day]["revenue_cents"] += cents
        ch = campaign_channel.get(campaign)
        if ch:
            by_channel.setdefault(ch, {"channel": ch, "clicks": 0, "leads": 0, "revenue_cents": 0})
            by_channel[ch]["revenue_cents"] += cents
        if creator_id:
            row = by_creator.setdefault(str(creator_id), {"creator_id": str(creator_id), "name": "",
                                                          "revenue_cents": 0, "subs": 0})
            row["revenue_cents"] += cents
            row["subs"] += int(new_subs or 0)
    if by_creator:
        names = dict(db.execute(select(Creator.id, Creator.display_name).where(
            Creator.tenant_id == tenant_id, Creator.id.in_([uuid.UUID(k) for k in by_creator]))).all())
        for k, row in by_creator.items():
            row["name"] = names.get(uuid.UUID(k), "")
    return {
        "range": range_key, "clicks": clicks, "leads": n_leads, "signed": signed, "subs": subs,
        "revenue_cents": revenue,
        "conversion": {"click_to_lead": _ratio(n_leads, clicks), "lead_to_signed": _ratio(signed, n_leads)},
        "by_channel": sorted(by_channel.values(), key=lambda r: (-r["revenue_cents"], -r["clicks"])),
        "by_creator": sorted(by_creator.values(), key=lambda r: -r["revenue_cents"]),
        "timeseries": [{"date": d.isoformat(), **v} for d, v in sorted(series.items())],
    }

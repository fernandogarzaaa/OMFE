"""Tracking-link clicks: attribution capture, daily rollups, optional CAPI on click."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..attribution import ClickContext, extract_click_context
from ..hashing import keyed_hash
from ..models import Click, ClickRollupDaily, ConversionEvent, TrackingLink
from ..settings import get_settings
from . import jobs
from .context import load_context
from .kpis import invalidate
from .live import record_activity


def find_link(db: Session, slug: str) -> TrackingLink | None:
    return db.scalar(
        select(TrackingLink).where(
            TrackingLink.slug == slug, TrackingLink.active.is_(True), TrackingLink.deleted_at.is_(None)
        )
    )


def _bump_rollup(db: Session, link: TrackingLink, is_bot: bool) -> None:
    day = datetime.now(UTC).date()
    vals = {
        "clicks": ClickRollupDaily.clicks + 1,
        "bot_clicks": ClickRollupDaily.bot_clicks + (1 if is_bot else 0),
    }
    res = db.execute(
        update(ClickRollupDaily)
        .where(ClickRollupDaily.link_id == link.id, ClickRollupDaily.day == day)
        .values(**vals)
    )
    if getattr(res, "rowcount", 0):
        return
    try:
        with db.begin_nested():
            db.add(
                ClickRollupDaily(
                    tenant_id=link.tenant_id,
                    link_id=link.id,
                    day=day,
                    clicks=1,
                    bot_clicks=1 if is_bot else 0,
                )
            )
            db.flush()
    except IntegrityError:  # another request created today's row first
        db.execute(
            update(ClickRollupDaily)
            .where(ClickRollupDaily.link_id == link.id, ClickRollupDaily.day == day)
            .values(**vals)
        )


def record_click(
    db: Session,
    link: TrackingLink,
    *,
    query: Mapping[str, str],
    cookies: Mapping[str, str],
    user_agent: str,
    referrer: str | None,
    ip: str,
) -> tuple[ClickContext, str]:
    """Persist a click; returns (context, event_id shared with the browser Pixel)."""
    ctx = extract_click_context(query, cookies, user_agent, referrer)
    salt = get_settings().pii_hash_salt.get_secret_value()
    event_id = uuid.uuid4().hex
    db.add(
        Click(
            tenant_id=link.tenant_id,
            link_id=link.id,
            visitor_hash=keyed_hash(f"{ip}|{user_agent}", salt) if ip else "",
            **{k: v for k, v in ctx.as_dict().items()},
        )
    )
    _bump_rollup(db, link, ctx.is_bot)
    if not ctx.is_bot:
        record_activity(
            db,
            link.tenant_id,
            "click",
            {
                "type": "click",
                "link_id": str(link.id),
                "slug": link.slug,
                "channel": link.channel,
                "device": ctx.device,
            },
        )
        jobs.emit_event(
            db,
            link.tenant_id,
            "link.clicked",
            {
                "link_id": str(link.id),
                "slug": link.slug,
                "channel": link.channel,
                "campaign": link.campaign,
                "creator_id": str(link.creator_id) if link.creator_id else None,
                "device": ctx.device,
            },
            event_id,
        )
        if link.capi_on_click:
            tctx = load_context(db, tenant_id=link.tenant_id)
            if tctx.config.features.meta_capi:
                db.add(
                    ConversionEvent(
                        tenant_id=link.tenant_id,
                        name="ViewContent",
                        event_id=event_id,
                        link_id=link.id,
                        source="server",
                    )
                )
                jobs.enqueue(
                    db,
                    link.tenant_id,
                    "meta_capi.send",
                    {
                        "event_name": "ViewContent",
                        "event_id": event_id,
                        "event_time": int(datetime.now(UTC).timestamp()),
                        "event_source_url": f"{get_settings().public_base_url}/l/{link.slug}",
                        "user": {"external_id": keyed_hash(f"{ip}|{user_agent}", salt)},
                        "client_ip": ip or None,
                        "user_agent": user_agent or "unknown",
                        "fbc": ctx.fbc,
                        "fbp": ctx.fbp,
                        "custom_data": {"content_name": link.campaign, "channel": link.channel},
                    },
                    f"capi:ViewContent:{event_id}",
                )
        invalidate(link.tenant_id)
    return ctx, event_id


def link_click_counts(db: Session, tenant_id: uuid.UUID, link_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    from sqlalchemy import func

    if not link_ids:
        return {}
    rows = db.execute(
        select(ClickRollupDaily.link_id, func.sum(ClickRollupDaily.clicks - ClickRollupDaily.bot_clicks))
        .where(ClickRollupDaily.tenant_id == tenant_id, ClickRollupDaily.link_id.in_(link_ids))
        .group_by(ClickRollupDaily.link_id)
    ).all()
    return {lid: int(n or 0) for lid, n in rows}

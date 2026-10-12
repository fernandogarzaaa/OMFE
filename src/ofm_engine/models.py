"""SQLAlchemy 2.0 ORM models.

Conventions
-----------
* UUID primary keys everywhere (native `uuid` on Postgres, CHAR(32) on SQLite).
* Every business table carries `tenant_id` (FK -> tenants, indexed). All service/route
  queries go through `TenantScope` helpers that filter on it; tests prove isolation.
* `created_at` / `updated_at` on every table, `deleted_at` soft delete where records are
  user-facing (creators, links, leads, users, webhook subscriptions).
* High-volume tables (`clicks`, `events`, `activity`) have no outbound cascades and are
  indexed on (tenant_id, created_at) so they can be range-partitioned by month on Postgres
  without schema changes; dashboards read the `click_rollups_daily` table instead.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)


# --------------------------------------------------------------------------- tenancy / auth
class Tenant(TimestampMixin, Base):
    """An agency workspace. Its YAML (clients/<slug>.yaml) is the base config; `overrides`
    holds edits made in the Settings UI (deep-merged on top)."""

    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = _pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class User(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)  # admin | member
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)  # envelope-encrypted
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # bump = revoke all

    tenant: Mapped[Tenant] = relationship()


class RefreshToken(TimestampMixin, Base):
    """Opaque refresh tokens, stored as SHA-256 hashes. Rotated on every use; reuse of a
    rotated token revokes the whole family (token theft detection)."""

    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    user_agent: Mapped[str] = mapped_column(String(200), default="", nullable=False)


class ApiKey(TimestampMixin, Base):
    """Machine credentials for n8n / Make / Zapier. Only a hash is stored; the plaintext
    is shown once at creation. Format: ofm_<prefix>_<secret>."""

    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="service", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TenantSecret(TimestampMixin, Base):
    """Integration credentials, envelope-encrypted (AES-256-GCM DEK wrapped by a Fernet KEK)."""

    __tablename__ = "tenant_secrets"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tenant_secret_name"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    hint: Mapped[str] = mapped_column(String(8), default="", nullable=False)  # last 2 chars max
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_tenant_created", "tenant_id", "created_at"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_type: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# --------------------------------------------------------------------------- creators / links
class Creator(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "creators"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_creator_slug"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    bio: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    avatar_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    bio_links: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    channels: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)


class TrackingLink(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "tracking_links"
    __table_args__ = (UniqueConstraint("slug", name="uq_tracking_link_slug"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("creators.id", ondelete="SET NULL"), index=True
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)  # instagram, tiktok, reddit, x ...
    campaign: Mapped[str] = mapped_column(String(120), nullable=False)  # joins to revenue CSV
    destination_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    capi_on_click: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    creator: Mapped[Creator | None] = relationship()


class Click(Base):
    """Raw click store (partition-ready: append-only, no cascades, time-indexed)."""

    __tablename__ = "clicks"
    __table_args__ = (
        Index("ix_clicks_tenant_created", "tenant_id", "created_at"),
        Index("ix_clicks_link_created", "link_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    link_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    visitor_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    utm_source: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    utm_medium: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    utm_campaign: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    utm_content: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    utm_term: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    fbclid: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    fbc: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    fbp: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    ttclid: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    referrer_host: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    device: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    os: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    browser: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ClickRollupDaily(Base):
    """Pre-aggregated clicks per link per day; updated transactionally with each click."""

    __tablename__ = "click_rollups_daily"
    __table_args__ = (UniqueConstraint("link_id", "day", name="uq_rollup_link_day"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    link_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tracking_links.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bot_clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# --------------------------------------------------------------------------- recruitment
class Lead(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_tenant_stage", "tenant_id", "stage"),
        Index("ix_leads_tenant_created", "tenant_id", "created_at"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_lead_idem"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(32))
    email_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    socials: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    followers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    total_followers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    earnings_range: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    age_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consent_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    variant: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    attribution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tier: Mapped[str] = mapped_column(String(8), default="cold", nullable=False)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ai_enrichment: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    stage: Mapped[str] = mapped_column(String(16), default="new", nullable=False)
    ghl_contact_id: Mapped[str | None] = mapped_column(String(64))
    ghl_opportunity_id: Mapped[str | None] = mapped_column(String(64))
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)  # shared with browser Pixel


class FunnelVariant(TimestampMixin, Base):
    __tablename__ = "funnel_variants"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_variant_key"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    headline: Mapped[str] = mapped_column(String(200), nullable=False)
    subhead: Mapped[str] = mapped_column(String(400), default="", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# --------------------------------------------------------------------------- events / revenue
class ConversionEvent(Base):
    """Server-side conversion events (also the CAPI send log). event_id is the dedupe key
    shared with the browser Pixel."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", "name", name="uq_event_dedupe"),
        Index("ix_events_tenant_created", "tenant_id", "created_at"),
        Index("ix_events_tenant_name", "tenant_id", "name"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="server", nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    link_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    variant: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    capi_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)


class RevenueRecord(TimestampMixin, Base):
    __tablename__ = "revenue_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "row_hash", name="uq_revenue_row"),
        Index("ix_revenue_tenant_day", "tenant_id", "day"),
        Index("ix_revenue_tenant_campaign", "tenant_id", "campaign"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    creator_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("creators.id", ondelete="SET NULL"))
    day: Mapped[date] = mapped_column(Date, nullable=False)
    campaign: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    gross: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    net: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    new_subs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_file: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AdSpend(TimestampMixin, Base):
    __tablename__ = "ad_spend"
    __table_args__ = (UniqueConstraint("tenant_id", "row_hash", name="uq_adspend_row"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    day: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    campaign: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    spend: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)


# --------------------------------------------------------------------------- outbox / ops
class OutboxJob(TimestampMixin, Base):
    """Transactional outbox + job queue. Rows are inserted in the same DB transaction as the
    business change that caused them, then relayed by the worker with retries/backoff."""

    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_outbox_idem"),
        Index("ix_outbox_status_run_after", "status", "run_after"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Activity(Base):
    """Live activity feed rows (drive the SSE dashboard; replayable via Last-Event-ID)."""

    __tablename__ = "activity"
    __table_args__ = (Index("ix_activity_tenant_seq", "tenant_id", "seq"),)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IntegrationLog(Base):
    __tablename__ = "integration_logs"
    __table_args__ = (Index("ix_intlog_tenant_created", "tenant_id", "created_at"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    adapter: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class WebhookSubscription(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "webhook_subscriptions"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    events: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class InboundWebhook(Base):
    """Replay protection + audit for inbound webhooks (GHL, n8n/Make)."""

    __tablename__ = "inbound_webhooks"
    __table_args__ = (UniqueConstraint("source", "dedupe_key", name="uq_inbound_dedupe"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

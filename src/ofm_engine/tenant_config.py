"""Per-tenant (per-agency) configuration.

Source of truth layering (later wins):
  1. defaults in these pydantic models
  2. clients/<slug>.yaml           (version-controlled, no secrets)
  3. tenants.overrides (DB JSON)   (edited in Settings UI by admins)

Secrets are *never* in YAML. They resolve from:
  1. tenant_secrets (envelope-encrypted, pasted in Settings > Integrations), else
  2. environment: CLIENT_<SLUG>_<NAME>, e.g. CLIENT_DEMO_AGENCY_GHL_TOKEN
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,62}$")

SECRET_NAMES = (
    "GHL_TOKEN",  # Private Integration token (pit-...) or OAuth sub-account access token
    "GHL_WORKFLOW_WEBHOOK_URL",  # GHL workflow "Inbound Webhook" trigger URL (contains a secret id)
    "GHL_INBOUND_SECRET",  # shared secret for GHL workflow "Custom Webhook" actions -> us
    "META_CAPI_TOKEN",
    "TIKTOK_ACCESS_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "SLACK_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
    "INBOUND_HMAC_SECRET",  # n8n/Make -> /api/v1/hooks/inbound HMAC secret
)


class Brand(BaseModel):
    name: str = "Your Agency"
    tagline: str = "We help creators grow."
    primary_color: str = "#8b5cf6"
    logo_url: str = ""
    support_email: str = ""

    @field_validator("primary_color")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            raise ValueError("primary_color must be #RRGGBB")
        return v


class Variant(BaseModel):
    key: str
    headline: str
    subhead: str = ""


class FunnelCopy(BaseModel):
    variants: list[Variant] = Field(
        default_factory=lambda: [
            Variant(
                key="A",
                headline="Grow your creator business with a team that's all-in",
                subhead="Management, marketing and strategy so you can focus on creating.",
            ),
        ]
    )
    cta: str = "Apply in 60 seconds"
    benefits: list[str] = Field(
        default_factory=lambda: ["Dedicated manager", "Growth strategy", "Transparent splits"]
    )
    earnings_ranges: list[str] = Field(
        default_factory=lambda: ["none", "0-1k", "1k-5k", "5k-10k", "10k-25k", "25k+"]
    )
    thank_you: str = "Thanks! Grab a time below for a quick intro call."
    booking_url: str = ""
    consent_text: str = (
        "I confirm I am 18 or older and agree to be contacted by SMS/email about management "
        "services. Msg & data rates may apply. Reply STOP to opt out."
    )
    consent_version: str = "2026-10-01"
    privacy_url: str = ""


class GHLConfig(BaseModel):
    location_id: str = ""
    pipeline_id: str = ""
    stages: dict[str, str] = Field(default_factory=dict)  # new/qualified/booked/signed/lost -> stage id
    workflow_id: str = ""
    calendar_id: str = ""
    custom_fields: dict[str, str] = Field(default_factory=dict)  # our key -> GHL field id or key
    base_tags: list[str] = Field(default_factory=lambda: ["ofm-recruit"])
    source: str = "OFM Engine funnel"


class MetaConfig(BaseModel):
    pixel_id: str = ""
    test_event_code: str = ""


class TikTokConfig(BaseModel):
    pixel_code: str = ""
    test_event_code: str = ""


class Features(BaseModel):
    ghl_sync: bool = True
    meta_capi: bool = True
    tiktok_events: bool = False
    ai_enrichment: bool = True
    ab_testing: bool = True
    outbound_webhooks: bool = True
    alerts: bool = True


class RevenueMapping(BaseModel):
    date: str = "date"
    creator: str = "creator"
    campaign: str = "campaign"
    gross: str = "gross"
    net: str = "net"
    new_subs: str = "new_subs"
    date_format: str = "%Y-%m-%d"
    net_ratio_if_missing: float = 0.8  # OF keeps 20%


class CreatorSeed(BaseModel):
    slug: str
    display_name: str
    bio: str = ""
    avatar_url: str = ""
    links: list[dict[str, str]] = Field(default_factory=list)
    channels: list[dict[str, Any]] = Field(default_factory=list)


class TenantConfig(BaseModel):
    slug: str
    name: str
    brand: Brand = Field(default_factory=Brand)
    funnel: FunnelCopy = Field(default_factory=FunnelCopy)
    ghl: GHLConfig = Field(default_factory=GHLConfig)
    meta: MetaConfig = Field(default_factory=MetaConfig)
    tiktok: TikTokConfig = Field(default_factory=TikTokConfig)
    features: Features = Field(default_factory=Features)
    scoring: dict[str, Any] = Field(default_factory=dict)
    target_timezones: list[str] = Field(default_factory=list)
    revenue_mapping: RevenueMapping = Field(default_factory=RevenueMapping)
    creators: list[CreatorSeed] = Field(default_factory=list)

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.fullmatch(v):
            raise ValueError("slug must be lowercase letters, digits and dashes")
        return v


# Fields admins may edit from the UI (everything else stays in YAML / code review)
EDITABLE_OVERRIDE_KEYS = {
    "brand",
    "funnel",
    "ghl",
    "meta",
    "tiktok",
    "features",
    "scoring",
    "target_timezones",
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_yaml(slug: str, clients_dir: Path) -> dict[str, Any]:
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("invalid slug")
    path = clients_dir / f"{slug}.yaml"
    if not path.exists():
        return {"slug": slug, "name": slug}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("slug", slug)
    data.setdefault("name", slug)
    return data


def build_config(slug: str, name: str, clients_dir: Path, overrides: dict[str, Any] | None) -> TenantConfig:
    base = load_yaml(slug, clients_dir)
    base.setdefault("name", name)
    safe_overrides = {k: v for k, v in (overrides or {}).items() if k in EDITABLE_OVERRIDE_KEYS}
    return TenantConfig.model_validate(deep_merge(base, safe_overrides))


def env_var_name(slug: str, secret: str) -> str:
    return f"CLIENT_{slug.upper().replace('-', '_')}_{secret}"

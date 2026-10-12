"""Integration settings (write-only) and connection tests.

Secret fields are stored envelope-encrypted in `tenant_secrets`; non-secret identifiers (pixel id,
location id ...) live in `tenants.overrides`. Neither is ever returned by the API — only `set: bool`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from ..adapters.http import IntegrationError
from ..adapters.meta_capi import CapiEvent
from ..adapters.tiktok import TikTokEvent
from ..tenant_config import deep_merge
from . import secrets as secret_store
from .context import TenantContext


@dataclass(frozen=True)
class Field_:
    name: str
    vault: str | None = None  # TenantSecret name
    config: tuple[str, str] | None = None  # (section, key) in tenant config overrides
    required: bool = True


PROVIDERS: dict[str, list[Field_]] = {
    "ghl": [
        Field_("token", vault="GHL_TOKEN"),
        Field_("location_id", config=("ghl", "location_id")),
        Field_("pipeline_id", config=("ghl", "pipeline_id"), required=False),
        Field_("workflow_id", config=("ghl", "workflow_id"), required=False),
        Field_("workflow_webhook_url", vault="GHL_WORKFLOW_WEBHOOK_URL", required=False),
    ],
    "meta_capi": [
        Field_("access_token", vault="META_CAPI_TOKEN"),
        Field_("pixel_id", config=("meta", "pixel_id")),
        Field_("test_event_code", config=("meta", "test_event_code"), required=False),
    ],
    "tiktok": [
        Field_("access_token", vault="TIKTOK_ACCESS_TOKEN"),
        Field_("pixel_code", config=("tiktok", "pixel_code")),
        Field_("test_event_code", config=("tiktok", "test_event_code"), required=False),
    ],
    "llm": [
        Field_("anthropic_api_key", vault="ANTHROPIC_API_KEY", required=False),
        Field_("openai_api_key", vault="OPENAI_API_KEY", required=False),
    ],
}


class UnknownField(ValueError):
    pass


def _is_set(ctx: TenantContext, f: Field_) -> bool:
    if f.vault:
        return bool(ctx.secret(f.vault))
    assert f.config is not None
    section = getattr(ctx.config, f.config[0])
    return bool(getattr(section, f.config[1], ""))


def _mode(ctx: TenantContext, provider: str) -> str:
    return {
        "ghl": lambda: ctx.ghl().mode,
        "meta_capi": lambda: ctx.meta().mode,
        "tiktok": lambda: ctx.tiktok().mode,
        "llm": lambda: ctx.llm().mode,
    }[provider]()


def status(ctx: TenantContext) -> list[dict[str, Any]]:
    out = []
    for provider, fields in PROVIDERS.items():
        fs = [
            {"name": f.name, "set": _is_set(ctx, f), "required": f.required, "secret": bool(f.vault)}
            for f in fields
        ]
        configured = all(x["set"] for x in fs if x["required"]) and (
            provider != "llm" or any(x["set"] for x in fs)
        )
        out.append(
            {"provider": provider, "configured": configured, "mode": _mode(ctx, provider), "fields": fs}
        )
    return out


def update(
    ctx: TenantContext, provider: str, values: dict[str, Any], actor_id: uuid.UUID | None
) -> list[str]:
    fields = {f.name: f for f in PROVIDERS[provider]}
    unknown = set(values) - set(fields)
    if unknown:
        raise UnknownField(f"unknown field(s): {', '.join(sorted(unknown))}")
    overrides: dict[str, Any] = {}
    changed = []
    for name, raw in values.items():
        f = fields[name]
        value = "" if raw is None else str(raw).strip()
        if len(value) > 4096:
            raise ValueError(f"{name} is too long")
        if f.vault:
            if value:
                secret_store.set_secret(ctx.db, ctx.tenant, f.vault, value, actor_id)
            else:
                secret_store.delete_secret(ctx.db, ctx.tenant, f.vault)
        else:
            assert f.config is not None
            overrides.setdefault(f.config[0], {})[f.config[1]] = value[:200]
        changed.append(name)
    if overrides:
        ctx.tenant.overrides = deep_merge(dict(ctx.tenant.overrides or {}), overrides)
    return changed


def test(ctx: TenantContext, provider: str) -> dict[str, Any]:
    import time

    try:
        if provider == "ghl":
            ghl = ctx.ghl()
            loc = ctx.call("ghl", "test", ghl.mode, ghl.get_location)
            return {
                "ok": True,
                "mode": ghl.mode,
                "detail": f"location {loc.get('location', {}).get('name', 'ok')}",
            }
        if provider == "meta_capi":
            meta = ctx.meta()
            ev = CapiEvent(
                event_name="PageView",
                event_id=f"test-{uuid.uuid4().hex[:12]}",
                action_source="system_generated",
                raw_user={"external_id": "ofm-engine-connection-test"},
            )
            res = ctx.call(
                "meta_capi",
                "test",
                meta.mode,
                lambda: meta.send([ev], test_event_code=ctx.config.meta.test_event_code or "TEST_OFM"),
            )
            return {"ok": True, "mode": meta.mode, "detail": f"events_received={res.get('events_received')}"}
        if provider == "tiktok":
            tt = ctx.tiktok()
            tev = TikTokEvent(
                event="ViewContent", event_id=f"test-{uuid.uuid4().hex[:12]}", event_time=int(time.time())
            )
            ctx.call("tiktok", "test", tt.mode, lambda: tt.send([tev]))
            return {"ok": True, "mode": tt.mode, "detail": "event accepted"}
        llm = ctx.llm()
        text = ctx.call("llm", "test", llm.mode, lambda: llm.complete("Reply with OK.", "ping", 10))
        return {"ok": True, "mode": llm.mode, "detail": text[:60]}
    except (IntegrationError, ValueError) as exc:
        return {"ok": False, "mode": _mode(ctx, provider), "detail": str(exc)[:300]}

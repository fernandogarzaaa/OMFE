"""TenantContext: one object carrying a tenant's effective config, secrets and adapter factory.

All services receive a TenantContext (never a bare tenant slug from user input), so every
query they make is scoped by `ctx.tenant_id`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.ghl import GHLClient, GHLCredentials
from ..adapters.http import IntegrationError
from ..adapters.llm import LLMClient, LLMConfig
from ..adapters.meta_capi import MetaCapiClient, MetaCredentials
from ..adapters.tiktok import TikTokClient, TikTokCredentials
from ..logging_setup import redact
from ..models import IntegrationLog, Tenant
from ..settings import get_settings
from ..tenant_config import TenantConfig, build_config
from . import secrets as secret_store

T = TypeVar("T")


class TenantNotFoundError(LookupError):
    pass


@dataclass
class TenantContext:
    db: Session
    tenant: Tenant
    config: TenantConfig

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id

    @property
    def slug(self) -> str:
        return self.tenant.slug

    def secret(self, name: str) -> str | None:
        return secret_store.get_secret(self.db, self.tenant, name)

    # --- adapter factories ---------------------------------------------------------
    def ghl(self) -> GHLClient:
        return GHLClient(GHLCredentials(token=self.secret("GHL_TOKEN"), location_id=self.config.ghl.location_id))

    def meta(self) -> MetaCapiClient:
        return MetaCapiClient(MetaCredentials(
            pixel_id=self.config.meta.pixel_id, access_token=self.secret("META_CAPI_TOKEN"),
            test_event_code=self.config.meta.test_event_code or None,
        ))

    def tiktok(self) -> TikTokClient:
        return TikTokClient(TikTokCredentials(
            pixel_code=self.config.tiktok.pixel_code, access_token=self.secret("TIKTOK_ACCESS_TOKEN"),
            test_event_code=self.config.tiktok.test_event_code or None,
        ))

    def llm(self) -> LLMClient:
        s = get_settings()
        provider = s.llm_provider
        if provider == "anthropic":
            key = self.secret("ANTHROPIC_API_KEY") or (s.anthropic_api_key.get_secret_value()
                                                     if s.anthropic_api_key else None)
            return LLMClient(LLMConfig("anthropic", key, s.anthropic_model))
        if provider == "openai":
            key = self.secret("OPENAI_API_KEY") or (s.openai_api_key.get_secret_value() if s.openai_api_key else None)
            return LLMClient(LLMConfig("openai", key, s.openai_model, s.openai_base_url))
        # auto-upgrade: a tenant that pasted a key gets live mode without touching env
        if key := self.secret("ANTHROPIC_API_KEY"):
            return LLMClient(LLMConfig("anthropic", key, s.anthropic_model))
        if key := self.secret("OPENAI_API_KEY"):
            return LLMClient(LLMConfig("openai", key, s.openai_model, s.openai_base_url))
        return LLMClient(LLMConfig("mock", None, "mock"))

    # --- integration call logging --------------------------------------------------
    def call(self, adapter: str, operation: str, mode: str, fn: Callable[[], T], detail: dict[str, Any] | None = None
             ) -> T:
        start = time.perf_counter()
        ok, status, err = True, None, None
        try:
            return fn()
        except IntegrationError as exc:
            ok, status, err = False, exc.status_code, str(exc)
            raise
        finally:
            info = dict(detail or {})
            if err:
                info["error"] = err
            self.db.add(IntegrationLog(
                tenant_id=self.tenant_id, adapter=adapter, operation=operation, mode=mode, ok=ok,
                status_code=status, duration_ms=int((time.perf_counter() - start) * 1000),
                detail=redact(info),
            ))


def load_context(db: Session, *, tenant_id: uuid.UUID | None = None, slug: str | None = None) -> TenantContext:
    stmt = select(Tenant).where(Tenant.active.is_(True))
    if tenant_id is not None:
        stmt = stmt.where(Tenant.id == tenant_id)
    elif slug is not None:
        stmt = stmt.where(Tenant.slug == slug)
    else:  # pragma: no cover - programming error
        raise ValueError("tenant_id or slug required")
    tenant = db.scalar(stmt)
    if tenant is None:
        raise TenantNotFoundError("tenant not found")
    cfg = build_config(tenant.slug, tenant.name, get_settings().clients_dir, tenant.overrides)
    return TenantContext(db=db, tenant=tenant, config=cfg)

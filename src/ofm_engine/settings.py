"""Application settings (12-factor, environment driven).

Every value has a safe default so the whole system boots in MOCK mode with no `.env`.
Secrets are never logged: `Settings.__repr__` masks every SecretStr.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into os.environ (without overriding real env) so per-client secrets such as
# CLIENT_ACME_GHL_TOKEN are visible to the secret resolver, not only to declared fields.
load_dotenv(override=False)

DEV_JWT_SECRET = "dev-insecure-jwt-secret-change-me-0123456789"  # noqa: S105 - dev only, refused in prod
DEV_MASTER_KEY = "ZGV2LW9ubHktbWFzdGVyLWtleS1jaGFuZ2UtbWUtISE="  # noqa: S105 - dev only, refused in prod


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- runtime ---------------------------------------------------------------
    env: str = Field(default="development", description="development | test | production")
    app_name: str = "OFM Engine"
    public_base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./ofm_engine.db"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle: int = 1800
    redis_url: str | None = None
    clients_dir: Path = Path("clients")
    sequences_dir: Path = Path("sequences")
    log_level: str = "INFO"
    log_json: bool = True
    auto_create_tables: bool = True  # SQLite dev convenience; Postgres uses Alembic
    run_worker_in_process: bool = False  # demo convenience; production runs `ofm worker`
    worker_poll_interval: float = 1.0
    worker_batch_size: int = 20
    metrics_token: SecretStr | None = None  # optional bearer token for /metrics

    # --- security --------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_issuer: str = "ofm-engine"
    access_token_ttl_seconds: int = 900  # 15 minutes
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14  # 14 days
    master_keys: SecretStr = SecretStr(DEV_MASTER_KEY)  # comma separated Fernet keys, first = primary
    pii_hash_salt: SecretStr = SecretStr("dev-pii-salt")
    cookie_secure: bool | None = None  # default: True in production
    cors_origins: list[str] = Field(default_factory=list)
    # argon2id cost (OWASP baseline m=19 MiB, t=2, p=1 is the floor; defaults are stronger).
    # Tests lower these via env (ARGON2_TIME_COST=1, ARGON2_MEMORY_COST=8) for speed.
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_cost: int = Field(default=65536, ge=8, description="KiB")
    argon2_parallelism: int = Field(default=4, ge=1)
    login_max_attempts: int = 5
    login_lockout_seconds: int = 900
    webhook_tolerance_seconds: int = 300
    allow_unsigned_ghl_webhooks_in_mock: bool = True
    outbound_allowed_hosts: list[str] = Field(default_factory=list)  # extra hosts for user webhooks
    outbound_allow_private: bool = False  # never enable in production
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: SecretStr | None = None
    bootstrap_tenant: str = "demo-agency"

    # --- rate limits (requests / minute / IP) ---------------------------------
    rate_limit_enabled: bool = True
    rate_limit_public_per_min: int = 30
    rate_limit_redirect_per_min: int = 240
    rate_limit_api_per_min: int = 600
    rate_limit_auth_per_min: int = 10

    # --- integrations ----------------------------------------------------------
    ghl_api_base: str = "https://services.leadconnectorhq.com"
    ghl_api_version: str = "2021-07-28"  # also accepts "v3" (released 2026-06-11)
    ghl_webhook_public_key: str | None = None  # override published Ed25519 key if rotated
    meta_graph_version: str = "v25.0"
    tiktok_api_base: str = "https://business-api.tiktok.com/open_api/v1.3"
    llm_provider: str = "mock"  # mock | anthropic | openai
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    http_timeout_seconds: float = 15.0
    http_max_retries: int = 4

    @field_validator("cors_origins", "outbound_allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.is_production if self.cookie_secure is None else self.cookie_secure

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def production_problems(self) -> list[str]:
        """Return a list of unsafe settings. Production boot refuses to start if any."""
        problems: list[str] = []
        if self.jwt_secret.get_secret_value() == DEV_JWT_SECRET or len(self.jwt_secret.get_secret_value()) < 32:
            problems.append("JWT_SECRET must be set to a random value of 32+ chars")
        if DEV_MASTER_KEY in self.master_keys.get_secret_value():
            problems.append("MASTER_KEYS must be set (generate with `ofm gen-key`)")
        if self.pii_hash_salt.get_secret_value() == "dev-pii-salt":
            problems.append("PII_HASH_SALT must be set")
        if self.outbound_allow_private:
            problems.append("OUTBOUND_ALLOW_PRIVATE must be false in production")
        if self.argon2_memory_cost < 19456 or self.argon2_time_cost < 2:
            problems.append("ARGON2_* cost below OWASP minimum (m>=19456 KiB, t>=2)")
        if self.is_sqlite:
            problems.append("DATABASE_URL must point at Postgres in production")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def env_secret(name: str) -> str | None:
    """Read a raw secret from the environment (used for per-client CLIENT_<SLUG>_* vars)."""
    val = os.environ.get(name)
    return val if val else None

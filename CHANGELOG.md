# Changelog

All notable changes to OFM Engine are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-13

First public release: an all-in-one marketing, tracking and automation toolkit for
OnlyFans management agencies. Every integration runs in mock mode until credentials are added.

### Added
- **Creator-recruitment funnel** at `/apply/{tenant}`: server-rendered, works without JavaScript,
  A/B headline variants (sticky cookie, `?v=` override), required 18+ confirmation and contact
  consent (versioned and timestamped), honeypot bot trap, per-IP rate limit, duplicate-email
  idempotency.
- **Transparent lead scoring** (0–100, hot/warm/cold) from audience size, platforms, stated
  earnings, contact details and target timezones; weights and tier cut-offs configurable per tenant.
- **GoHighLevel sync** via the LeadConnector v2 API: contact upsert, tags (`tier-*` + base tags),
  score-breakdown note, optional workflow enrolment, opportunity create/update mapped to the
  recruitment pipeline stages.
- **Tracking links** `/l/{slug}` with UTM pass-through, `fbclid`→`_fbc` / `_fbp` cookies, bot
  filtering, daily click rollups, optional Meta Pixel interstitial sharing an `event_id` with a
  server-side CAPI `ViewContent`; link-in-bio pages at `/c/{tenant}/{creator}`.
- **Meta Conversions API and TikTok Events API** adapters with PII normalisation + SHA-256
  hashing and browser/server event de-duplication.
- **Revenue CSV import** with column mapping, date formats, content-hash de-duplication and
  sample exports; **KPI API** (7/30/90-day) and a **live dashboard** over Server-Sent Events with
  resumable additive deltas.
- **Automations**: signed outbound webhooks (`X-OFM-Signature`, HMAC-SHA256, 5-minute window),
  signed inbound callbacks with replay protection, scoped API keys, ready-to-import n8n workflows,
  a Make blueprint and Zapier recipes; Slack/Discord alerts for hot and warm leads.
- **Durable job queue**: transactional outbox, `SKIP LOCKED` claiming on Postgres, exponential
  backoff, idempotency keys, dead-letter status with one-click retry.
- **AI copy assistant** (Anthropic, OpenAI or deterministic mock) for recruitment ads, headlines,
  follow-ups and captions, with guardrails that refuse explicit content, minors and impersonation.
- **Multi-tenant RBAC**: admin / member roles plus service API keys, tenant-scoped queries,
  audit log, TOTP two-factor authentication.
- **Security**: argon2id passwords with lockout, 15-minute JWT access cookies, rotating refresh
  tokens with reuse detection, CSRF double-submit, CSP with per-request nonces, HSTS,
  SSRF-guarded outbound URLs, envelope-encrypted integration secrets (AES-256-GCM data keys
  wrapped by rotatable `MASTER_KEYS`), log redaction, production boot refusing dev secrets.
- **React dashboard** (Vite, TypeScript, Tailwind, TanStack Query, Recharts) with an in-browser
  mock API for demos (`npm run dev:mock`).
- **Operations**: `ofm` CLI, Alembic migrations, Prometheus `/metrics`, `/healthz` + `/readyz`,
  digest-pinned non-root Docker image, docker compose stack, Render free-tier blueprint,
  CI (SQLite + Postgres 17, coverage gate, smoke, e2e, pip-audit, bandit, npm audit, gitleaks) and
  a tag-triggered release that publishes `ghcr.io/fernandogarzaaa/omfe` (amd64 + arm64).

[Unreleased]: https://github.com/fernandogarzaaa/OMFE/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/fernandogarzaaa/OMFE/releases/tag/v1.0.0

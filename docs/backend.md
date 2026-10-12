# OFM Engine backend — operations

## Run locally (mock mode, $0)
```bash
uv venv && uv pip install -e ".[dev]"
ofm seed-demo                      # demo tenant + admin@example.com / member@example.com (passwords printed)
RUN_WORKER_IN_PROCESS=true ofm serve
open http://localhost:8000/apply/demo   # public funnel;  /  = dashboard (SPA)
ofm seed-demo --stream             # synthetic live events for the dashboard
scripts/smoke.sh                   # end-to-end smoke test
```

## Production
* Postgres + `ofm migrate`; set `JWT_SECRET`, `MASTER_KEYS`, `PII_HASH_SALT` (boot refuses dev defaults),
  `CORS_ORIGINS` if the SPA is on another origin, `METRICS_TOKEN`.
* Run `ofm serve` (API) and `ofm worker` (jobs) as separate processes. Postgres LISTEN/NOTIFY fans live
  events out to every API instance; `REDIS_URL` additionally shares rate limits.
* Key rotation: prepend a new key to `MASTER_KEYS`, run `ofm rotate-keys`, then drop the old key.

## Architecture
* `app.py` factory → middleware (security headers + CSP nonce, rate limits, metrics) → routers in `api/routes`.
* `api/deps.py`: principal from `access_token` cookie (JWT, 15 min, token_version revocation) or `X-API-Key`;
  RBAC via `require_admin/require_member/require_user`; CSRF double-submit on cookie-auth writes.
* Every query filters on `principal.tenant_id`; public routes resolve the tenant from the slug.
* `services/jobs.py`: transactional outbox + worker (SKIP LOCKED on Postgres), retries with exponential
  backoff, idempotency keys, failures recorded in a fresh transaction after rollback.
* Integrations (GHL, Meta CAPI, TikTok, LLM, Slack/Discord, outbound webhooks) are mock by default and switch
  to live per tenant once credentials are configured.

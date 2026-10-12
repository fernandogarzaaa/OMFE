# Quality

How OFM Engine is tested, how to reproduce every number in the README's **Quality** section, and
what CI enforces on each push and pull request to `main`.

## At a glance

| Layer | Tool | Where | CI |
|---|---|---|---|
| Backend unit + API tests | pytest, respx, coverage (branch) | `tests/` | SQLite **and** PostgreSQL 17, fails under 90% coverage |
| Frontend unit tests | vitest + Testing Library (jsdom) | `frontend/src/**/__tests__` | ✅ |
| Contract tests | pytest + vitest | `tests/test_integration_contract.py`, `frontend/src/lib/__tests__` | ✅ |
| Smoke (black-box, mock mode) | bash + curl | `scripts/smoke.sh` | ✅ |
| End-to-end (built SPA ↔ real backend) | bash + curl | `scripts/e2e_ui.sh` | ✅ against the Docker image |
| Static analysis | ruff, mypy, tsc | `src`, `tests`, `quality`, `frontend` | ✅ |
| Security scanning | pip-audit, bandit, npm audit, gitleaks | locks, `src`, full git history | ✅ |
| Mutation testing | testteeth (Python), Stryker (frontend) | `quality/mutation/`, `frontend/stryker.config.json` | on demand |
| Hardened test gate | `quality/gate.py` + `quality/test-manifest.json` | `quality/` | ✅ (SQLite job) |

## Setup

```bash
make install            # or see CONTRIBUTING.md for the manual commands
```

Tests run with cheap argon2 parameters set by the test configuration; production refuses those
values at boot.

## Backend tests

```bash
make test-py
# = .venv/bin/python -m pytest --cov=ofm_engine --cov-branch --cov-report=term --cov-fail-under=90
```

Against PostgreSQL, exactly as CI's `backend (postgres)` job:

```bash
docker run -d --rm --name ofm-pg -p 5432:5432 \
  -e POSTGRES_USER=ofm -e POSTGRES_PASSWORD=ofm -e POSTGRES_DB=ofm_test postgres:17-alpine
TEST_DATABASE_URL=postgresql://ofm:ofm@127.0.0.1:5432/ofm_test make test-py
```

The suite covers auth flows (lockout, TOTP, refresh rotation and reuse), RBAC and cross-tenant
isolation, the public funnel and redirects, jobs/outbox semantics, CLI and migrations (including
a Postgres schema-drift check), integration adapters against their mocks, security primitives
(crypto, signing, SSRF, tokens, passwords) and the API/SPA contract.

## Frontend tests

```bash
make test-web           # = cd frontend && npx vitest run
cd frontend && npx tsc --noEmit
```

## Smoke test

```bash
make smoke              # = PYTHON=.venv/bin/python OFM=.venv/bin/ofm scripts/smoke.sh
```

Boots the server on a throwaway SQLite database (port 8765) with an in-process worker and checks
health/readiness, funnel variants and the 18+ text, CSP, link-in-bio, tracking redirect + UTM +
`_fbc`, the Pixel interstitial, applications (including rejection without 18+/consent), login,
KPIs, CSRF enforcement, integrations in mock mode, the AI guardrail, the outbox draining a CAPI
job, and the SSE stream. Prints `smoke: N passed, 0 failed`.

## End-to-end (SPA ↔ backend)

```bash
make e2e                # builds the SPA, starts a seeded server on :8766, runs scripts/e2e_ui.sh
# or against any running server seeded with `ofm seed-demo --known-passwords`:
BASE=http://127.0.0.1:8000 scripts/e2e_ui.sh
```

Exercises every `/api/v1` route the UI calls with the same cookies and CSRF header the browser
sends, the SPA routes, and the SSE stream (47 checks). CI runs it against the freshly built
Docker image after verifying the container runs as UID 10001, becomes healthy, and refuses to
start in production mode with dev secrets.

## Lint and type checks

```bash
make lint               # ruff check, ruff format --check, mypy src, tsc --noEmit
make fmt                # auto-fix
```

## Security scans

```bash
.venv/bin/pip-audit -r requirements/runtime.lock --require-hashes --disable-pip
.venv/bin/pip-audit -r requirements/dev.lock --require-hashes --disable-pip
.venv/bin/bandit -q -r src -ll
cd frontend && npm audit --omit=dev --audit-level=moderate
gitleaks git . --redact          # reviewed false positives are fingerprinted in .gitleaksignore
```

## Mutation testing

Mutation testing checks that the tests actually fail when the code is wrong.

**Python — [testteeth](https://github.com/fernandogarzaaa/testteeth)** on the security and core
modules (`crypto`, `passwords`, `signing`, `ssrf`, `tokens`, `deps`, `hashing`, `meta_capi`,
`scoring`, `attribution`), each mutated against the test files that target it:

```bash
quality/mutation/run.sh before            # baseline, all modules
quality/mutation/run.sh after crypto ssrf # re-grade selected modules after adding tests
MUTANT_CAP=60 quality/mutation/run.sh after
```

Mutants are sampled evenly with a fixed cap so `before` and `after` grade the same set. Results
are written to `quality/mutation/results/<module>.<phase>.{txt,json}`; the summary lines are
`Mutants:` and `Mutation score:`.

**Frontend — Stryker** on the API client (`src/lib/api.ts`):

```bash
cd frontend && npx stryker run
```

## Hardened test gate

CI's SQLite job runs `python quality/gate.py --manifest quality/test-manifest.json` after the
test suite. The gate and its manifest live under `quality/`; see those files for exactly what it
enforces and for the latest results.

## Updating the README numbers

The README's Quality table sits between `<!-- QUALITY:START -->` and `<!-- QUALITY:END -->` so
it can be regenerated by tooling. Update it from:

- pytest's final line and the coverage `TOTAL` row (both database jobs),
- vitest's `Tests … passed` line,
- the `smoke:` and `e2e_ui:` summary lines,
- the security job (all scanners exit 0),
- the `Mutation score:` lines from `quality/mutation/results/*.after.txt`,
- the gate's summary output.

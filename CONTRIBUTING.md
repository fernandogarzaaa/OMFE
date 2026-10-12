# Contributing to OFM Engine

Thanks for helping. This guide covers the local setup, the checks every change must pass, and
the conventions the codebase follows.

## Ground rules

- **Mock mode first.** Every integration (GoHighLevel, Meta CAPI, TikTok, LLMs, outbound
  webhooks, Slack/Discord) must keep working offline with no credentials. New adapters ship
  with an in-process mock and are switched to live per tenant only when credentials are set.
- **Tenant isolation.** Every query on business data filters on the caller's `tenant_id`
  (services receive a `TenantContext`, never a raw slug from user input). Add a cross-tenant
  test for any new endpoint.
- **No secrets in the repo.** Tenant YAML in `clients/` holds identifiers only; tokens go in
  Settings → Integrations (envelope-encrypted) or `CLIENT_<SLUG>_<NAME>` environment variables.
- **Compliance is a feature.** Do not add OnlyFans scraping, automated fan messaging, or
  anything that writes *as* a creator. Read [docs/compliance.md](docs/compliance.md) first.
- Security issues go through [SECURITY.md](SECURITY.md), not public issues.

## Setup

Requirements: Python 3.12 (3.11+ supported), Node 22, GNU Make; Docker optional.

```bash
make install        # .venv with hash-pinned dev deps + editable package, and frontend npm ci
make dev            # backend on :8000 (SQLite, in-process worker) — seed first with `make seed`
npm --prefix frontend run dev:mock   # or: SPA alone with the in-browser mock API on :5173
```

## Before you open a pull request

```bash
make lint           # ruff check + ruff format --check + mypy + tsc
make test           # pytest (branch coverage, fails under 90%) + vitest
make smoke          # boots the server in mock mode and exercises the main flows
make e2e            # builds the SPA and checks every API route it calls (needs a seeded server)
```

To run the backend suite against Postgres as CI does:

```bash
docker run -d --rm -p 5432:5432 -e POSTGRES_USER=ofm -e POSTGRES_PASSWORD=ofm -e POSTGRES_DB=ofm_test postgres:17-alpine
TEST_DATABASE_URL=postgresql://ofm:ofm@127.0.0.1:5432/ofm_test make test
```

See [docs/quality.md](docs/quality.md) for mutation testing and the hardened test gate.

## Conventions

- **Python**: ruff (line length 110, rules `E,F,W,I,B,UP,S,SIM,RUF`), mypy with
  `check_untyped_defs`; type-annotate new code. Errors leave the API as
  `{"error": {"code", "message"}}`; list endpoints return `{items, next_cursor}`.
- **API changes** update [docs/api-contract.md](docs/api-contract.md), the serializers, the
  frontend types in `frontend/src/lib/types.ts`, and `tests/test_integration_contract.py`
  together.
- **Schema changes** need an Alembic revision in `alembic/versions/` (SQLite dev databases are
  created automatically; Postgres is migrated with `ofm migrate`).
- **Dependencies** are hash-pinned in `requirements/*.lock` and `frontend/package-lock.json`;
  explain any new dependency in the PR.
- **Commits**: short imperative subject with a type prefix (`feat:`, `fix:`, `docs:`, `ci:`,
  `deps:`, `ops:`, `build:`, `test:`). Add user-facing changes under `## [Unreleased]` in
  [CHANGELOG.md](CHANGELOG.md).

## Releasing (maintainers)

1. Bump the version in `pyproject.toml`, `src/ofm_engine/__init__.py` and
   `frontend/package.json` (the release workflow checks all three match the tag).
2. Move `## [Unreleased]` entries to `## [X.Y.Z] - YYYY-MM-DD` in `CHANGELOG.md`; the release
   notes are extracted from that section by `scripts/changelog_notes.py`.
3. Tag `vX.Y.Z` on `main` and push the tag. The workflow builds the wheel/sdist with the SPA
   inside, publishes `ghcr.io/fernandogarzaaa/omfe` for amd64 and arm64, and drafts the GitHub
   release with checksums.

By contributing you agree your work is released under the [MIT License](LICENSE).

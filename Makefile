# OFM Engine — developer shortcuts. Run `make help` for the list.
# Everything runs in MOCK mode by default: no provider calls leave the machine.

SHELL        := /bin/bash
PYTHON       ?= python3.12
VENV         ?= .venv
PY           := $(VENV)/bin/python
OFM          := $(VENV)/bin/ofm
NPM          ?= npm
FRONTEND     := frontend
IMAGE        ?= ghcr.io/fernandogarzaaa/omfe:local
PORT         ?= 8000
E2E_PORT     ?= 8766

.DEFAULT_GOAL := help
.PHONY: help install install-py install-web dev dev-web test test-py test-web lint fmt smoke e2e \
        frontend build docker docker-up docker-down migrate seed stream doctor clean

help: ## Show this help
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z_-]+:.*## /{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}' $(MAKEFILE_LIST)

# ------------------------------------------------------------------ setup
install: install-py install-web ## Create .venv (hash-pinned dev deps) and install frontend deps

install-py:
	test -x $(PY) || $(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --require-hashes -r requirements/build.lock
	$(PY) -m pip install --require-hashes -r requirements/dev.lock
	$(PY) -m pip install --no-deps --no-build-isolation -e .

install-web:
	cd $(FRONTEND) && $(NPM) ci --no-audit --no-fund

# ------------------------------------------------------------------ run
dev: ## Run the API + SPA on :8000 with an in-process worker (SQLite, mock mode)
	RUN_WORKER_IN_PROCESS=true $(OFM) serve --reload --port $(PORT)

dev-web: ## Run the Vite dev server on :5173 (proxies /api, /l, /apply to :8000)
	cd $(FRONTEND) && $(NPM) run dev

seed: ## Seed the demo tenant (admin@demo.agency / member@demo.agency, password demo-pass-2026)
	$(OFM) seed-demo --known-passwords

stream: ## Emit synthetic live events for the dashboard (Ctrl+C to stop)
	$(OFM) seed-demo --stream

migrate: ## Apply Alembic migrations to DATABASE_URL (Postgres)
	$(OFM) migrate

doctor: ## Check configuration, database and integration modes
	$(OFM) doctor

# ------------------------------------------------------------------ quality
test: test-py test-web ## Backend (pytest + branch coverage) and frontend (vitest) tests

test-py:
	$(PY) -m pytest --cov=ofm_engine --cov-branch --cov-report=term --cov-fail-under=90

test-web:
	cd $(FRONTEND) && npx vitest run

lint: ## ruff, ruff format --check, mypy, tsc
	$(VENV)/bin/ruff check src tests quality
	$(VENV)/bin/ruff format --check src tests quality
	$(VENV)/bin/mypy src
	cd $(FRONTEND) && npx tsc --noEmit

fmt: ## Auto-fix lint and formatting
	$(VENV)/bin/ruff check --fix src tests quality
	$(VENV)/bin/ruff format src tests quality

smoke: ## Boot a throwaway mock-mode server and exercise the main flows
	PYTHON=$(PY) OFM=$(OFM) scripts/smoke.sh

e2e: frontend ## Build the SPA, boot a seeded server and check every API route the UI calls
	@set -e; work=$$(mktemp -d); \
	export DATABASE_URL="sqlite:///$$work/e2e.db" ENV=development LOG_JSON=false LOG_LEVEL=WARNING \
	  RUN_WORKER_IN_PROCESS=true RATE_LIMIT_AUTH_PER_MIN=100 ARGON2_TIME_COST=2 ARGON2_MEMORY_COST=19456; \
	$(OFM) seed-demo --known-passwords >/dev/null; \
	$(OFM) serve --port $(E2E_PORT) >"$$work/server.log" 2>&1 & srv=$$!; \
	trap 'kill $$srv 2>/dev/null; rm -rf "$$work"' EXIT; \
	for i in $$(seq 1 50); do curl -fs http://127.0.0.1:$(E2E_PORT)/healthz >/dev/null && break; sleep 0.2; done; \
	BASE=http://127.0.0.1:$(E2E_PORT) scripts/e2e_ui.sh

# ------------------------------------------------------------------ build / ship
frontend: ## Build the SPA into src/ofm_engine/web/dist
	cd $(FRONTEND) && $(NPM) run build

build: frontend ## Build the wheel + sdist (SPA and migrations included) into dist/
	$(PY) -m pip wheel --no-deps --no-build-isolation -w dist .

docker: ## Build the production image ($(IMAGE))
	docker build -t $(IMAGE) .

docker-up: ## Start app + worker + Postgres with docker compose (mock mode, demo seeded)
	docker compose up --build

docker-down: ## Stop the compose stack
	docker compose down

clean: ## Remove build artefacts and caches
	rm -rf dist build .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find src -name __pycache__ -type d -prune -exec rm -rf {} +

# syntax=docker/dockerfile:1.7
# OFM Engine — multi-stage image: Node builds the SPA, Python builds a venv from hash-pinned
# locks, and a slim non-root runtime serves both. Base images are pinned by digest
# (multi-arch index digests; refresh with `docker buildx imagetools inspect <tag>`).
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

# ---------------------------------------------------------------- 1. frontend
FROM ${NODE_IMAGE} AS web
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --ignore-scripts
COPY frontend/ ./
# vite writes to ../src/ofm_engine/web/dist (= /build/src/ofm_engine/web/dist)
RUN npm run build

# ---------------------------------------------------------------- 2. python venv
FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
RUN python -m venv /opt/venv
COPY requirements/runtime.lock requirements/build.lock requirements/
RUN /opt/venv/bin/pip install --require-hashes -r requirements/runtime.lock \
 && pip install --require-hashes -r requirements/build.lock
COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY --from=web /build/src/ofm_engine/web/dist ./src/ofm_engine/web/dist
# Build the wheel with the system interpreter (hatchling from build.lock stays out of the runtime
# venv), then install it with --no-deps: every runtime dependency came from the hashed lock above.
RUN pip wheel --no-deps --no-build-isolation -w /tmp/wheels . \
 && /opt/venv/bin/pip install --no-deps /tmp/wheels/ofm_engine-*.whl \
 && /opt/venv/bin/ofm version \
 && test -f /opt/venv/lib/python3.12/site-packages/ofm_engine/web/dist/index.html \
 && test -f /opt/venv/lib/python3.12/site-packages/ofm_engine/_migrations/alembic.ini

# ---------------------------------------------------------------- 3. runtime
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="OFM Engine" \
      org.opencontainers.image.description="Tools for OnlyFans Managers: recruitment funnel, GHL sync, Meta/TikTok CAPI, attribution, revenue KPIs" \
      org.opencontainers.image.source="https://github.com/fernandogarzaaa/OMFE" \
      org.opencontainers.image.licenses="MIT"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENV=production \
    PORT=8000 \
    DATABASE_URL=sqlite:////app/data/ofm_engine.db \
    CLIENTS_DIR=/app/clients \
    SEQUENCES_DIR=/app/sequences \
    LOG_JSON=true
RUN groupadd --system --gid 10001 ofm \
 && useradd --system --uid 10001 --gid ofm --home-dir /app --no-create-home --shell /usr/sbin/nologin ofm \
 && mkdir -p /app/data \
 && chown -R 10001:10001 /app/data
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY clients ./clients
COPY sequences ./sequences
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/ofm-entrypoint
USER 10001:10001
EXPOSE 8000
VOLUME ["/app/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request as u; sys.exit(0 if u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=4).status == 200 else 1)"]
ENTRYPOINT ["ofm-entrypoint"]
CMD ["serve"]

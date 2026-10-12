"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import middleware
from .api.errors import error_body, install_handlers
from .api.routes import admin, auth, creators, data, health, leads, links, public, users
from .db import create_all, get_engine
from .logging_setup import configure_logging
from .security.ratelimit import make_limiter
from .services import live
from .settings import get_settings

log = logging.getLogger(__name__)
DIST_DIR = Path(__file__).resolve().parent / "web" / "dist"

LANDING_FALLBACK = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>OFM Engine</title></head>
<body style="font-family:system-ui;background:#0b0b10;color:#eee;max-width:640px;margin:10vh auto;
padding:0 20px">
<h1>OFM Engine</h1><p>Tools for OnlyFans managers: recruitment funnel, lead scoring, GoHighLevel sync,
Meta CAPI attribution, tracking links and revenue KPIs.</p>
<p>The dashboard build was not found. Build the frontend into <code>src/ofm_engine/web/dist</code>.
API docs: <a style="color:#a78bfa" href="/api/docs">/api/docs</a></p></body></html>"""


def _bootstrap_admin() -> None:
    s = get_settings()
    if not (s.bootstrap_admin_email and s.bootstrap_admin_password):
        return
    from sqlalchemy import select

    from .db import session_scope
    from .models import Tenant, User
    from .security.passwords import hash_password

    with session_scope() as db:
        if db.scalar(select(User).where(User.email == s.bootstrap_admin_email.lower())):
            return
        tenant = db.scalar(select(Tenant).where(Tenant.slug == s.bootstrap_tenant))
        if tenant is None:
            tenant = Tenant(slug=s.bootstrap_tenant, name=s.bootstrap_tenant.replace("-", " ").title())
            db.add(tenant)
            db.flush()
        db.add(
            User(
                tenant_id=tenant.id,
                email=s.bootstrap_admin_email.lower(),
                name="Admin",
                role="admin",
                password_hash=hash_password(s.bootstrap_admin_password.get_secret_value()),
            )
        )
        log.info("bootstrap admin created")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    live.broker.bind_loop(asyncio.get_running_loop())
    if s.is_sqlite and s.auto_create_tables:
        create_all()
    _bootstrap_admin()
    if not s.is_sqlite or s.redis_url:
        live.start_listeners()  # pragma: no cover
    worker = None
    if s.run_worker_in_process:
        from .services.jobs import start_in_process_worker

        worker = start_in_process_worker()
    try:
        yield
    finally:
        if worker is not None:
            worker[1].set()


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    if s.is_production:
        problems = s.production_problems()
        if problems:
            raise RuntimeError("refusing to start in production: " + "; ".join(problems))
    get_engine()
    app = FastAPI(
        title=s.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    install_handlers(app)
    middleware.install(app, make_limiter(s.redis_url))
    if s.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=s.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token", "X-API-Key", "Last-Event-ID"],
            max_age=600,
        )
    api = APIRouter(prefix="/api/v1")
    for r in (
        auth.router,
        users.router,
        creators.router,
        leads.router,
        links.router,
        data.router,
        admin.router,
        public.api_router,
        health.router,
    ):
        api.include_router(r)
    app.include_router(api)
    app.include_router(health.router)
    app.include_router(public.page_router)
    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    index = DIST_DIR / "index.html"
    if (DIST_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.api_route(
        "/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False
    )
    def api_404(rest: str) -> JSONResponse:
        return JSONResponse(error_body("not_found", "no such API route"), status_code=404)

    @app.get("/{rest:path}", include_in_schema=False)
    def spa(rest: str, request: Request) -> Response:
        if rest:
            candidate = (DIST_DIR / rest).resolve()
            if candidate.is_file() and DIST_DIR.resolve() in candidate.parents:
                return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(LANDING_FALLBACK)

"""/healthz, /readyz, /metrics (bearer token, read from the environment on every request)."""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from ... import __version__
from ...db import session_factory
from ...settings import Settings
from ..errors import error_body
from ..middleware import REGISTRY

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/readyz")
def readyz() -> JSONResponse:
    try:
        db = session_factory()()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "unavailable", "db": "down"}, status_code=503)
    return JSONResponse({"status": "ready", "db": "ok"})


def _metrics_token() -> str | None:
    # Read per request (not cached) so the token can be rotated without a restart.
    raw = os.environ.get("METRICS_TOKEN")
    if raw is None:
        tok = Settings().metrics_token
        raw = tok.get_secret_value() if tok else None
    return raw or None


@router.get("/metrics")
def metrics(request: Request) -> Response:
    token = _metrics_token()
    if not token:
        return JSONResponse(error_body("not_found", "metrics disabled (set METRICS_TOKEN)"), status_code=404)
    auth = request.headers.get("authorization", "")
    supplied = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not supplied or not hmac.compare_digest(supplied.encode(), token.encode()):
        return JSONResponse(error_body("unauthorized", "invalid metrics token"), status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

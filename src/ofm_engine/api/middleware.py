"""Security headers (CSP w/ per-request nonce, HSTS ...), rate limiting, request metrics,
CSRF cookie seeding."""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CollectorRegistry, Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware

from ..security import tokens
from ..security.ratelimit import RateLimiter
from ..settings import get_settings
from .errors import error_body

REGISTRY = CollectorRegistry()
REQUESTS = Counter(
    "ofm_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY
)
LATENCY = Histogram("ofm_http_request_seconds", "HTTP latency", ["method", "route"], registry=REGISTRY)
RATE_LIMITED = Counter(
    "ofm_rate_limited_total", "Requests rejected by rate limiter", ["bucket"], registry=REGISTRY
)

Call = Callable[[Request], Awaitable[Response]]


def build_csp(nonce: str) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}' https://connect.facebook.net https://analytics.tiktok.com",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: https:",
            "font-src 'self' data:",
            "connect-src 'self' https://www.facebook.com https://analytics.tiktok.com",
            "frame-src https://www.facebook.com",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
        ]
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Call) -> Response:
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        resp = await call_next(request)
        s = get_settings()
        h = resp.headers
        h.setdefault("Content-Security-Policy", build_csp(nonce))
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if s.is_production or s.secure_cookies:
            h.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")
        if request.url.path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        # seed the double-submit CSRF cookie for browsers that don't have one yet
        if not request.cookies.get(tokens.CSRF_COOKIE) and tokens.CSRF_COOKIE not in h.get("set-cookie", ""):
            ctype = h.get("content-type", "")
            if request.url.path.startswith("/api/") or ctype.startswith("text/html"):
                from .cookies import set_csrf_cookie

                set_csrf_cookie(resp, tokens.new_csrf_token())
        return resp


def _bucket(path: str) -> str | None:
    if path.startswith("/api/v1/auth/login") or path.startswith("/api/v1/auth/refresh"):
        return "auth"
    if (
        path.startswith("/api/v1/public/")
        or path.startswith("/api/v1/hooks/")
        or (path.startswith("/apply/"))
    ):
        return "public"
    if path.startswith("/l/"):
        return "redirect"
    if path.startswith("/api/"):
        return "api"
    return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, limiter: RateLimiter) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next: Call) -> Response:
        s = get_settings()
        bucket = _bucket(request.url.path)
        if s.rate_limit_enabled and bucket and request.method != "OPTIONS":
            if bucket == "public" and request.method == "GET":
                limit = s.rate_limit_redirect_per_min  # page views are cheap; submissions are strict
            else:
                limit = {
                    "auth": s.rate_limit_auth_per_min,
                    "public": s.rate_limit_public_per_min,
                    "redirect": s.rate_limit_redirect_per_min,
                    "api": s.rate_limit_api_per_min,
                }[bucket]
            ip = request.client.host if request.client else "unknown"
            if not self.limiter.allow(f"{bucket}:{ip}", limit):
                RATE_LIMITED.labels(bucket).inc()
                return JSONResponse(
                    error_body("rate_limited", "too many requests"),
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Call) -> Response:
        start = time.perf_counter()
        status = 500
        try:
            resp = await call_next(request)
            status = resp.status_code
            return resp
        finally:
            route = request.scope.get("route")
            name = getattr(route, "path", None) or (
                "spa" if not request.url.path.startswith("/api") else "unmatched"
            )
            REQUESTS.labels(request.method, name, str(status)).inc()
            LATENCY.labels(request.method, name).observe(time.perf_counter() - start)


def install(app: FastAPI, limiter: RateLimiter) -> None:
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)
    app.add_middleware(SecurityHeadersMiddleware)

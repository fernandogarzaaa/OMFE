"""Uniform error envelope: {"error": {"code": str, "message": str}}."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str = "", headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code.replace("_", " ")
        self.headers = headers


def error_body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    err.update(extra)
    return {"error": err}


_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}


def not_found(what: str = "resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "error")
        msg = exc.detail if isinstance(exc.detail, str) else code
        return JSONResponse(
            error_body(code, msg), status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for e in exc.errors()[:10]:
            loc = ".".join(str(p) for p in e.get("loc", ()) if p not in ("body", "query", "path"))
            details.append({"field": loc, "message": str(e.get("msg", ""))})
        first = details[0] if details else {"field": "", "message": "invalid request"}
        msg = f"{first['field']}: {first['message']}" if first["field"] else first["message"]
        return JSONResponse(error_body("validation_error", msg, details=details), status_code=422)

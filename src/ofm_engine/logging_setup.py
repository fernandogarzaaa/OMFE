"""Structured JSON logging with PII / secret redaction.

Redaction runs on the formatted message *and* on structured `extra` fields, so a token that
slips into an f-string is still masked before it reaches stdout / a log shipper.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

SENSITIVE_KEYS = {
    "password", "passwd", "secret", "token", "access_token", "refresh_token", "authorization",
    "api_key", "apikey", "x-api-key", "cookie", "set-cookie", "email", "phone", "em", "ph",
    "totp", "code", "client_secret", "private_key",
}

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)bearer\s+[a-z0-9._\-~+/=]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)(access_token|api_key|token|secret|password)=([^&\s\"']+)"), r"\1=[REDACTED]"),
    (re.compile(r"\bpit-[a-z0-9\-]{8,}\b", re.I), "[REDACTED_GHL_TOKEN]"),
    (re.compile(r"\bsk-[a-zA-Z0-9_\-]{8,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bofm_[a-zA-Z0-9]{6,}_[a-zA-Z0-9_\-]{10,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bEAA[a-zA-Z0-9]{20,}\b"), "[REDACTED_META_TOKEN]"),
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"(?<![\w.])\+?\d[\d\s().\-]{8,}\d(?![\w.])"), "[REDACTED_PHONE]"),
]


def redact_text(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively redact sensitive keys and patterns in dicts / lists / strings."""
    if _depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if str(k).lower() in SENSITIVE_KEYS:
                out[k] = "[REDACTED]"
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(value, list | tuple):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS}
        if extras:
            payload.update(redact(extras))
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if json_logs else RedactingTextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

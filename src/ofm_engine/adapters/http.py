"""Resilient, SSRF-guarded HTTP client shared by every integration adapter.

* Exponential backoff with jitter on 408/429/5xx and transport errors.
* Honors `Retry-After` (seconds) on 429/503, capped.
* Every URL is checked against the integration host allowlist before a socket is opened
  (unless the adapter is in MOCK mode, which uses an in-process transport).
* Optional `Idempotency-Key` header passthrough for providers that support it.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from ..security.ssrf import check_integration_url
from ..settings import get_settings

RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 30.0


class IntegrationError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    @property
    def retryable(self) -> bool:
        return self.status_code is None or self.status_code in RETRY_STATUSES


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 20.0, jitter: float = 0.25) -> float:
    delay = min(cap, base * (2**attempt))
    return delay + random.uniform(0, delay * jitter)


def retry_after_seconds(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, min(MAX_RETRY_AFTER, float(value)))
    except ValueError:
        return None


class HttpClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int | None = None,
        timeout: float | None = None,
        guard: bool = True,
    ) -> None:
        s = get_settings()
        self.max_retries = s.http_max_retries if max_retries is None else max_retries
        self.sleep = sleep
        self.guard = guard and transport is None
        self._client = httpx.Client(
            transport=transport, timeout=timeout or s.http_timeout_seconds, follow_redirects=False
        )

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        if self.guard:
            check_integration_url(url)
        hdrs = dict(headers or {})
        if idempotency_key:
            hdrs["Idempotency-Key"] = idempotency_key
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.request(method, url, headers=hdrs, json=json, params=params)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise IntegrationError(f"transport error: {exc.__class__.__name__}") from exc
                self.sleep(backoff_delay(attempt))
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                wait = retry_after_seconds(resp)
                self.sleep(wait if wait is not None else backoff_delay(attempt))
                continue
            if resp.status_code >= 400:
                try:
                    body: Any = resp.json()
                except ValueError:
                    body = resp.text[:500]
                raise IntegrationError(f"HTTP {resp.status_code}", status_code=resp.status_code, body=body)
            return resp
        raise IntegrationError("retries exhausted") from last_exc  # pragma: no cover

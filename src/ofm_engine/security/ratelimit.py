"""Fixed-window rate limiter with pluggable backend.

* `MemoryBackend` (default, per-process) — fine for one instance / dev.
* `RedisBackend` (when REDIS_URL is set) — shared across horizontally scaled API pods.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol


class Backend(Protocol):
    def hit(self, key: str, window: int) -> int: ...


class MemoryBackend:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, tuple[int, int]] = {}

    def hit(self, key: str, window: int) -> int:
        bucket = int(time.time()) // window
        with self._lock:
            b, count = self._counts.get(key, (bucket, 0))
            if b != bucket:
                count = 0
            count += 1
            self._counts[key] = (bucket, count)
            if len(self._counts) > 50_000:  # bound memory
                self._counts = {k: v for k, v in self._counts.items() if v[0] == bucket}
            return count


class RedisBackend:  # pragma: no cover - exercised only when Redis is configured
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url)

    def hit(self, key: str, window: int) -> int:
        bucket = int(time.time()) // window
        k = f"rl:{key}:{bucket}"
        pipe = self._r.pipeline()
        pipe.incr(k)
        pipe.expire(k, window + 1)
        count, _ = pipe.execute()
        return int(count)


class RateLimiter:
    def __init__(self, backend: Backend | None = None) -> None:
        self.backend: Backend = backend or MemoryBackend()

    def allow(self, key: str, limit: int, window: int = 60) -> bool:
        if limit <= 0:
            return True
        return self.backend.hit(key, window) <= limit


def make_limiter(redis_url: str | None) -> RateLimiter:
    if redis_url:  # pragma: no cover
        try:
            return RateLimiter(RedisBackend(redis_url))
        except Exception:  # noqa: BLE001 - fall back to memory if redis lib/conn missing
            return RateLimiter()
    return RateLimiter()

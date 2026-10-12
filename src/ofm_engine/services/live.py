"""Live activity feed + pub/sub for the real-time dashboard.

Write path: `record_activity()` inserts an Activity row in the *same transaction* as the
business change. Wake-ups are delivered by:
  * Postgres: `pg_notify('ofm_activity', <tenant_id>)` inside the transaction — delivered
    by Postgres only on COMMIT, to every API instance LISTENing ($0, no Redis needed).
  * Redis (optional, REDIS_URL): PUBLISH after commit.
  * Fallback: in-process asyncio wake-up after commit.
Read path: SSE handlers wait for a wake-up (or a short timeout) and then read rows with
seq > Last-Event-ID, so missed notifications can never lose data — notify is only a hint.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from ..models import Activity
from ..settings import get_settings

log = logging.getLogger(__name__)
CHANNEL = "ofm_activity"


class Broker:
    """Process-local fan-out of tenant wake-ups to waiting SSE coroutines."""

    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Event]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def wake(self, tenant_id: str) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            waiters = list(self._waiters.get(tenant_id, ()))
        for ev in waiters:
            loop.call_soon_threadsafe(ev.set)

    async def wait(self, tenant_id: str, timeout: float) -> bool:
        ev = asyncio.Event()
        with self._lock:
            self._waiters[tenant_id].add(ev)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False
        finally:
            with self._lock:
                self._waiters[tenant_id].discard(ev)


broker = Broker()
_redis_client: Any = None


def _publish_after_commit(session: Session, tenant_id: str) -> None:
    pending: set[str] = session.info.setdefault("ofm_pending_wakeups", set())
    pending.add(tenant_id)


@event.listens_for(Session, "after_commit")
def _flush_wakeups(session: Session) -> None:
    pending: set[str] = session.info.pop("ofm_pending_wakeups", set())
    for tid in pending:
        broker.wake(tid)
        if _redis_client is not None:  # pragma: no cover - requires redis
            with contextlib.suppress(Exception):
                _redis_client.publish(CHANNEL, tid)


def record_activity(db: Session, tenant_id: uuid.UUID, kind: str, data: dict[str, Any]) -> None:
    db.add(Activity(tenant_id=tenant_id, kind=kind, data=data))
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":  # pragma: no cover - exercised on Postgres
        db.execute(text("SELECT pg_notify(:c, :t)"), {"c": CHANNEL, "t": str(tenant_id)})
    _publish_after_commit(db, str(tenant_id))


def fetch_since(db: Session, tenant_id: uuid.UUID, since: int, limit: int = 100) -> list[Activity]:
    stmt = (
        select(Activity)
        .where(Activity.tenant_id == tenant_id, Activity.seq > since)
        .order_by(Activity.seq)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def latest_seq(db: Session, tenant_id: uuid.UUID) -> int:
    row = db.scalar(
        select(Activity.seq).where(Activity.tenant_id == tenant_id).order_by(Activity.seq.desc()).limit(1)
    )
    return int(row or 0)


def start_listeners() -> list[threading.Thread]:  # pragma: no cover - needs PG/Redis
    """Start background listeners that translate PG NOTIFY / Redis messages into wake-ups."""
    s = get_settings()
    threads: list[threading.Thread] = []
    if s.redis_url:
        try:
            import redis

            global _redis_client
            _redis_client = redis.Redis.from_url(s.redis_url)

            def _redis_loop() -> None:
                ps = redis.Redis.from_url(s.redis_url).pubsub()  # type: ignore[arg-type]
                ps.subscribe(CHANNEL)
                for msg in ps.listen():
                    if msg.get("type") == "message":
                        broker.wake(msg["data"].decode())

            threads.append(threading.Thread(target=_redis_loop, daemon=True, name="redis-listener"))
        except Exception:
            log.warning("redis unavailable; falling back to polling")
    if s.database_url.startswith(("postgres", "postgresql")):

        def _pg_loop() -> None:
            import psycopg

            from ..db import _normalize_url

            dsn = _normalize_url(s.database_url).replace("postgresql+psycopg://", "postgresql://")
            while True:
                try:
                    with psycopg.connect(dsn, autocommit=True) as conn:
                        conn.execute(f"LISTEN {CHANNEL}")
                        for note in conn.notifies():
                            broker.wake(note.payload)
                except Exception:
                    log.warning("pg listener reconnecting")
                    threading.Event().wait(2.0)

        threads.append(threading.Thread(target=_pg_loop, daemon=True, name="pg-listener"))
    for t in threads:
        t.start()
    return threads

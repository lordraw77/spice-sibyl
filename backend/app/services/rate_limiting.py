"""Rate limiting behind an interface, with a shared-store backend.

Roadmap v2 § 3, P2. Two sliding windows existed, each a private dict in the
module that used it: per-user API throttling (``dependencies/rate_limit.py``)
and per-host throttling of ``http.request`` (``workflow/nodes/io.py``). Both
counted only what one process had seen, so N instances allowed N times the
configured rate.

They now share one interface with two implementations:

* ``InMemoryRateLimiter`` — the historical behaviour, and still the default.
  Correct and free for a single instance.
* ``DatabaseRateLimiter`` — one row per admission in ``rate_limit_hits``, so
  every instance counts against the same window. Costs a write per admission;
  worth it when the limit protects something outside the process (a paid API,
  a partner's endpoint) rather than this process's own CPU.

Select with ``RATE_LIMIT_BACKEND=memory|database``.

Two verbs, because the callers want different things from a full window: the
API must answer immediately with 429 (``try_admit``), the workflow node wants
to be throttled rather than fail (``admit``, which waits and reports how long).

A third pair, ``record``/``count``, exists for the login lockout (audit 2.5):
there the interesting event is a *failure*, and the check has to happen before
the attempt is made, so counting and consuming cannot be the same call.
"""

import asyncio
import logging
import time
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class RateLimiter(Protocol):
    """A sliding window of ``limit`` admissions per ``window`` seconds."""

    async def try_admit(self, bucket: str, limit: int, window: float) -> float | None:
        """Admit immediately, or report how long the caller would have to wait.

        Returns None when admitted, else the seconds until a slot frees up.
        """

    async def admit(self, bucket: str, limit: int, window: float) -> float:
        """Wait for a slot, then admit. Returns the seconds actually waited."""

    async def record(self, bucket: str) -> None:
        """Note that an event happened, without consuming an admission slot."""

    async def count(self, bucket: str, window: float) -> int:
        """How many events were recorded in the last ``window`` seconds."""


#: How long recorded events stay countable. Bounds the memory/table growth and
#: caps the longest lockout tier that can be expressed.
EVENT_RETENTION = 3600.0


class InMemoryRateLimiter:
    """Per-process sliding window. Uses the monotonic clock: immune to NTP steps."""

    def __init__(self) -> None:
        #: bucket → admission timestamps inside the current window.
        self.hits: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def try_admit(self, bucket: str, limit: int, window: float) -> float | None:
        async with self._lock:
            now = time.monotonic()
            kept = [t for t in self.hits.get(bucket, []) if now - t < window]
            if len(kept) < limit:
                kept.append(now)
                self.hits[bucket] = kept
                return None
            self.hits[bucket] = kept
            return max(0.0, kept[0] + window - now)

    async def admit(self, bucket: str, limit: int, window: float) -> float:
        waited = 0.0
        while True:
            delay = await self.try_admit(bucket, limit, window)
            if delay is None:
                return waited
            delay = max(0.05, delay)
            await asyncio.sleep(delay)
            waited += delay

    async def record(self, bucket: str) -> None:
        async with self._lock:
            now = time.monotonic()
            kept = [t for t in self.hits.get(bucket, []) if now - t < EVENT_RETENTION]
            kept.append(now)
            self.hits[bucket] = kept

    async def count(self, bucket: str, window: float) -> int:
        async with self._lock:
            now = time.monotonic()
            return sum(1 for t in self.hits.get(bucket, []) if now - t < window)


class DatabaseRateLimiter:
    """Window shared by every instance through the ``rate_limit_hits`` table.

    Uses wall time, since the rows outlive the process that wrote them. Expired
    rows for the bucket are deleted on each admission, which keeps the table
    proportional to the active buckets rather than to total traffic.
    """

    def __init__(self, connect=None) -> None:
        # Injectable so tests (and any future non-pool caller) can hand in their
        # own connection factory.
        self._connect = connect

    async def _with_db(self, fn):
        if self._connect is not None:
            db = await self._connect()
            try:
                return await fn(db)
            finally:
                await db.close()
        from app.db import pool

        async with pool.connection() as db:
            return await fn(db)

    async def try_admit(self, bucket: str, limit: int, window: float) -> float | None:
        async def _attempt(db):
            now = time.time()
            await db.execute(
                "DELETE FROM rate_limit_hits WHERE bucket = ? AND at <= ?",
                (bucket, now - window),
            )
            async with db.execute(
                "SELECT COUNT(*), MIN(at) FROM rate_limit_hits WHERE bucket = ?", (bucket,)
            ) as cursor:
                count, oldest = await cursor.fetchone()
            if count < limit:
                await db.execute(
                    "INSERT INTO rate_limit_hits (bucket, at) VALUES (?, ?)", (bucket, now)
                )
                await db.commit()
                return None
            await db.commit()
            return max(0.0, (oldest or now) + window - now)

        return await self._with_db(_attempt)

    async def admit(self, bucket: str, limit: int, window: float) -> float:
        waited = 0.0
        while True:
            delay = await self.try_admit(bucket, limit, window)
            if delay is None:
                return waited
            delay = max(0.05, delay)
            await asyncio.sleep(delay)
            waited += delay

    async def record(self, bucket: str) -> None:
        async def _write(db):
            now = time.time()
            await db.execute(
                "DELETE FROM rate_limit_hits WHERE bucket = ? AND at <= ?",
                (bucket, now - EVENT_RETENTION),
            )
            await db.execute(
                "INSERT INTO rate_limit_hits (bucket, at) VALUES (?, ?)", (bucket, now)
            )
            await db.commit()

        await self._with_db(_write)

    async def count(self, bucket: str, window: float) -> int:
        async def _read(db):
            async with db.execute(
                "SELECT COUNT(*) FROM rate_limit_hits WHERE bucket = ? AND at > ?",
                (bucket, time.time() - window),
            ) as cursor:
                (count,) = await cursor.fetchone()
            return int(count)

        return await self._with_db(_read)


_memory_limiter = InMemoryRateLimiter()
_database_limiter = DatabaseRateLimiter()


def get_limiter() -> RateLimiter:
    """The limiter selected by RATE_LIMIT_BACKEND (unknown value → memory)."""
    backend = (getattr(settings, "rate_limit_backend", "memory") or "memory").lower()
    if backend == "database":
        return _database_limiter
    if backend != "memory":
        logger.warning("Unknown RATE_LIMIT_BACKEND=%r; falling back to memory", backend)
    return _memory_limiter

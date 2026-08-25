"""SSE event bus for workflow runs, behind an interface.

Roadmap v2 § 3, P2. The bus used to be three module functions over a dict of
subscriber queues: correct, and invisible to any other instance. A browser
streaming ``GET /runs/{id}/stream`` from instance A saw nothing at all when the
run executed on instance B.

Two implementations now sit behind ``EventBus``:

* ``InMemoryEventBus`` — the historical behaviour, byte for byte, and still the
  default. Zero latency, zero I/O, single process.
* ``DatabaseEventBus`` — publishes into ``run_events`` and gives each subscriber
  a queue fed by a poller that tails the table from the row id current at
  subscription time. Every instance sees every event, at the cost of one insert
  per event and one small indexed query per subscriber per tick.

Select with ``WORKFLOW_BUS_BACKEND=memory|database``.

The module-level ``subscribe`` / ``unsubscribe`` / ``publish`` keep working and
delegate to the selected bus, because the engine and the SSE endpoint call them
directly (``engine.subscribe``) and publishing happens from synchronous code
paths inside node execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class EventBus(Protocol):
    """Fan-out of run events to whoever is streaming that run."""

    def subscribe(self, run_id: str) -> asyncio.Queue:
        """A queue that will receive this run's events from now on."""

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        """Stop feeding a queue and release whatever backs it."""

    def publish(self, run_id: str, event: dict) -> None:
        """Deliver an event to every current subscriber. Never blocks."""


class InMemoryEventBus:
    """Single-process fan-out: a list of queues per run id."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        queues = self._subscribers.get(run_id)
        if not queues:
            return
        if queue in queues:
            queues.remove(queue)
        if not queues:
            self._subscribers.pop(run_id, None)

    def publish(self, run_id: str, event: dict) -> None:
        for queue in self._subscribers.get(run_id, []):
            queue.put_nowait(event)


class DatabaseEventBus:
    """Cross-instance fan-out through the ``run_events`` table.

    ``publish`` stays non-blocking by scheduling the insert on the running loop;
    ordering per run is preserved because the writes are appended to a single
    per-bus queue drained in order.
    """

    #: How often a subscriber's poller looks for new rows.
    POLL_SECONDS = 0.25

    def __init__(self, connect=None, poll_seconds: float | None = None) -> None:
        self._connect = connect
        self._poll_seconds = poll_seconds or self.POLL_SECONDS
        self._pollers: dict[int, asyncio.Task] = {}
        self._writes: asyncio.Queue | None = None
        self._writer: asyncio.Task | None = None

    # ── plumbing ────────────────────────────────────────────────────────────

    async def _with_db(self, fn):
        """Run ``fn(db)`` on a pooled connection (or the injected one, in tests)."""
        if self._connect is not None:
            db = await self._connect()
            try:
                return await fn(db)
            finally:
                await db.close()
        from app.db import pool

        async with pool.connection() as db:
            return await fn(db)

    async def _insert(self, run_id: str, event: dict) -> None:
        async def _write(db):
            await db.execute(
                "INSERT INTO run_events (run_id, payload, created_at) VALUES (?, ?, ?)",
                (run_id, json.dumps(event), int(time.time())),
            )
            await db.commit()

        await self._with_db(_write)

    async def _drain_writes(self) -> None:
        assert self._writes is not None
        while True:
            run_id, event = await self._writes.get()
            try:
                await self._insert(run_id, event)
            except Exception:  # noqa: BLE001 — a dropped event must not kill the bus
                logger.exception("run event insert failed run_id=%s", run_id)
            finally:
                self._writes.task_done()

    async def _last_row_id(self) -> int:
        async def _read(db):
            async with db.execute("SELECT COALESCE(MAX(id), 0) FROM run_events") as cursor:
                (last,) = await cursor.fetchone()
            return last

        return await self._with_db(_read)

    async def _poll(self, run_id: str, queue: asyncio.Queue, after: int) -> None:
        cursor_id = after
        while True:
            try:
                async def _read(db):
                    async with db.execute(
                        "SELECT id, payload FROM run_events WHERE run_id = ? AND id > ? "
                        "ORDER BY id",
                        (run_id, cursor_id),
                    ) as cur:
                        return await cur.fetchall()

                rows = await self._with_db(_read)
                for row in rows:
                    cursor_id = row[0]
                    try:
                        queue.put_nowait(json.loads(row[1]))
                    except json.JSONDecodeError:
                        logger.warning("skipping malformed run event id=%s", row[0])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — keep tailing across transient errors
                logger.exception("run event poll failed run_id=%s", run_id)
            await asyncio.sleep(self._poll_seconds)

    # ── EventBus ────────────────────────────────────────────────────────────

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        async def _start():
            # Start from the current tip: a subscriber gets what happens next,
            # matching the in-memory bus rather than replaying the run.
            after = await self._last_row_id()
            self._pollers[id(queue)] = loop.create_task(self._poll(run_id, queue, after))

        loop.create_task(_start())
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        task = self._pollers.pop(id(queue), None)
        if task is not None:
            task.cancel()

    async def aclose(self) -> None:
        """Stop the writer and every poller. For shutdown and for tests.

        Pending writes are drained first so an event published just before
        shutdown is not lost.
        """
        if self._writes is not None and self._writer is not None and not self._writer.done():
            await self._writes.join()
        for task in list(self._pollers.values()):
            task.cancel()
        self._pollers.clear()
        if self._writer is not None:
            self._writer.cancel()
            self._writer = None
        self._writes = None

    def publish(self, run_id: str, event: dict) -> None:
        if self._writes is None:
            self._writes = asyncio.Queue()
        if self._writer is None or self._writer.done():
            self._writer = asyncio.get_running_loop().create_task(self._drain_writes())
        self._writes.put_nowait((run_id, event))


_memory_bus = InMemoryEventBus()
_database_bus = DatabaseEventBus()


def get_bus() -> EventBus:
    """The bus selected by WORKFLOW_BUS_BACKEND (unknown value → memory)."""
    backend = (getattr(settings, "workflow_bus_backend", "memory") or "memory").lower()
    if backend == "database":
        return _database_bus
    if backend != "memory":
        logger.warning("Unknown WORKFLOW_BUS_BACKEND=%r; falling back to memory", backend)
    return _memory_bus


def subscribe(run_id: str) -> asyncio.Queue:
    return get_bus().subscribe(run_id)


def unsubscribe(run_id: str, queue: asyncio.Queue) -> None:
    get_bus().unsubscribe(run_id, queue)


def publish(run_id: str, event: dict) -> None:
    get_bus().publish(run_id, event)

"""Multi-instance coordination (roadmap v2 § 3, P2).

Leader election, the shared rate-limit window and the database event bus. Each
test drives two "instances" against one database, which is the situation the
in-memory versions cannot handle.
"""

import asyncio
import time

import aiosqlite
import pytest

from app.db import migrations
from app.services import coordination
from app.services.rate_limiting import DatabaseRateLimiter, InMemoryRateLimiter
from app.workflow.bus import DatabaseEventBus, InMemoryEventBus


@pytest.fixture()
def db_path(tmp_path):
    """A database with just the coordination tables applied."""
    path = str(tmp_path / "coord.db")

    async def _prepare():
        async with aiosqlite.connect(path) as conn:
            await migrations.apply(conn)

    asyncio.new_event_loop().run_until_complete(_prepare())
    return path


def _connector(path):
    async def _connect():
        return await aiosqlite.connect(path)

    return _connect


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── leader election ─────────────────────────────────────────────────────────

def test_only_one_instance_holds_the_lease(db_path):
    async def _drive():
        async with aiosqlite.connect(db_path) as db:
            first = await coordination.acquire(db, "duty", 60, owner="instance-a")
            second = await coordination.acquire(db, "duty", 60, owner="instance-b")
            return first, second, await coordination.current_leader(db, "duty")

    first, second, leader = _run(_drive())
    assert first is True
    assert second is False, "a second instance must not take a live lease"
    assert leader == "instance-a"


def test_the_leader_renews_its_own_lease(db_path):
    async def _drive():
        async with aiosqlite.connect(db_path) as db:
            await coordination.acquire(db, "duty", 60, owner="instance-a")
            return await coordination.acquire(db, "duty", 60, owner="instance-a")

    assert _run(_drive()) is True


def test_an_expired_lease_is_taken_over(db_path):
    async def _drive():
        async with aiosqlite.connect(db_path) as db:
            await coordination.acquire(db, "duty", 60, owner="instance-a")
            # Simulate instance-a dying: its lease is now in the past.
            await db.execute(
                "UPDATE instance_leases SET expires_at = ? WHERE name = 'duty'",
                (time.time() - 1,),
            )
            await db.commit()
            taken = await coordination.acquire(db, "duty", 60, owner="instance-b")
            return taken, await coordination.current_leader(db, "duty")

    taken, leader = _run(_drive())
    assert taken is True
    assert leader == "instance-b"


def test_releasing_hands_the_duty_over_immediately(db_path):
    async def _drive():
        async with aiosqlite.connect(db_path) as db:
            await coordination.acquire(db, "duty", 3600, owner="instance-a")
            await coordination.release(db, "duty", owner="instance-a")
            return (await coordination.current_leader(db, "duty"),
                    await coordination.acquire(db, "duty", 60, owner="instance-b"))

    leader, taken = _run(_drive())
    assert leader is None
    assert taken is True


def test_release_by_a_non_holder_is_ignored(db_path):
    async def _drive():
        async with aiosqlite.connect(db_path) as db:
            await coordination.acquire(db, "duty", 3600, owner="instance-a")
            await coordination.release(db, "duty", owner="instance-b")
            return await coordination.current_leader(db, "duty")

    assert _run(_drive()) == "instance-a"


# ── rate limiting ───────────────────────────────────────────────────────────

def test_in_memory_limiter_admits_up_to_the_limit():
    limiter = InMemoryRateLimiter()

    async def _drive():
        return [await limiter.try_admit("b", 2, 60.0) for _ in range(3)]

    first, second, third = _run(_drive())
    assert first is None and second is None
    assert third is not None and third > 0


def test_database_limiter_shares_the_window_across_instances(db_path):
    """The whole point: two limiters, one window."""
    instance_a = DatabaseRateLimiter(connect=_connector(db_path))
    instance_b = DatabaseRateLimiter(connect=_connector(db_path))

    async def _drive():
        return (await instance_a.try_admit("host", 2, 60.0),
                await instance_b.try_admit("host", 2, 60.0),
                await instance_b.try_admit("host", 2, 60.0))

    first, second, third = _run(_drive())
    assert first is None and second is None
    assert third is not None, "the third call exceeded the shared limit"


def test_database_limiter_frees_the_slot_once_the_window_passes(db_path):
    limiter = DatabaseRateLimiter(connect=_connector(db_path))

    async def _drive():
        blocked = None
        await limiter.try_admit("host", 1, 0.2)
        blocked = await limiter.try_admit("host", 1, 0.2)
        waited = await limiter.admit("host", 1, 0.2)
        return blocked, waited

    blocked, waited = _run(_drive())
    assert blocked is not None
    assert waited > 0, "admit() waits instead of failing"


def test_in_memory_limiter_is_blind_to_other_instances():
    """Documents why the database backend exists at all."""
    async def _drive():
        return (await InMemoryRateLimiter().try_admit("b", 1, 60.0),
                await InMemoryRateLimiter().try_admit("b", 1, 60.0))

    first, second = _run(_drive())
    assert first is None and second is None


# ── event bus ───────────────────────────────────────────────────────────────

def test_in_memory_bus_delivers_to_every_subscriber():
    bus = InMemoryEventBus()

    async def _drive():
        a, b = bus.subscribe("run-1"), bus.subscribe("run-1")
        other = bus.subscribe("run-2")
        bus.publish("run-1", {"type": "node"})
        return a.get_nowait(), b.get_nowait(), other.empty()

    a_event, b_event, other_empty = _run(_drive())
    assert a_event == b_event == {"type": "node"}
    assert other_empty, "events must not leak across runs"


def test_in_memory_bus_stops_after_unsubscribe():
    bus = InMemoryEventBus()

    async def _drive():
        queue = bus.subscribe("run-1")
        bus.unsubscribe("run-1", queue)
        bus.publish("run-1", {"type": "node"})
        return queue.empty()

    assert _run(_drive())


def test_database_bus_delivers_across_instances(db_path):
    """A subscriber on one instance sees an event published by another."""
    publisher = DatabaseEventBus(connect=_connector(db_path), poll_seconds=0.02)
    subscriber = DatabaseEventBus(connect=_connector(db_path), poll_seconds=0.02)

    async def _drive():
        queue = subscriber.subscribe("run-1")
        await asyncio.sleep(0.1)  # let the poller register its starting point
        publisher.publish("run-1", {"type": "node", "id": "n1"})
        try:
            return await asyncio.wait_for(queue.get(), timeout=5)
        finally:
            subscriber.unsubscribe("run-1", queue)
            await publisher.aclose()
            await subscriber.aclose()

    assert _run(_drive()) == {"type": "node", "id": "n1"}


def test_database_bus_keeps_events_of_other_runs_out(db_path):
    bus = DatabaseEventBus(connect=_connector(db_path), poll_seconds=0.02)

    async def _drive():
        queue = bus.subscribe("run-1")
        await asyncio.sleep(0.1)
        bus.publish("run-2", {"type": "node"})
        bus.publish("run-1", {"type": "mine"})
        try:
            return await asyncio.wait_for(queue.get(), timeout=5)
        finally:
            bus.unsubscribe("run-1", queue)
            await bus.aclose()

    assert _run(_drive()) == {"type": "mine"}


def test_bus_selection_falls_back_to_memory(monkeypatch):
    from app.workflow import bus as bus_module

    monkeypatch.setattr(bus_module.settings, "workflow_bus_backend", "nonsense")
    assert isinstance(bus_module.get_bus(), InMemoryEventBus)
    monkeypatch.setattr(bus_module.settings, "workflow_bus_backend", "database")
    assert isinstance(bus_module.get_bus(), DatabaseEventBus)


def test_limiter_selection_falls_back_to_memory(monkeypatch):
    from app.services import rate_limiting

    monkeypatch.setattr(rate_limiting.settings, "rate_limit_backend", "nonsense")
    assert isinstance(rate_limiting.get_limiter(), InMemoryRateLimiter)
    monkeypatch.setattr(rate_limiting.settings, "rate_limit_backend", "database")
    assert isinstance(rate_limiting.get_limiter(), DatabaseRateLimiter)

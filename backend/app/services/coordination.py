"""Leader election for duties that exactly one instance may perform.

Roadmap v2 § 3, P2 — "EventBus/rate-limit/scheduler dietro interfaccia +
leader election". The schedule poll loop is the motivating case: two instances
polling the same database would each see the same due trigger and fire the
workflow twice.

The mechanism is a lease row per duty. Acquiring is a single conditional write,
so it stays correct under SQLite's one-writer rule without a second service:

    UPDATE instance_leases SET owner = ?, expires_at = ?
     WHERE name = ? AND (owner = ? OR expires_at <= ?)

Either the row is ours (renewal) or its lease has expired (takeover); anything
else leaves the row untouched and the caller learns it is not the leader. A
crashed leader is replaced after at most ``ttl`` seconds, and a single-instance
deployment — the default — always wins, so behaviour there is unchanged.

The clock is the database's own ``time.time()`` as seen by each instance, so
instances must agree on wall time to within a fraction of the TTL. That is the
same assumption the scheduler already makes about cron.
"""

import logging
import os
import time
import uuid

import aiosqlite

logger = logging.getLogger(__name__)

#: Identity of this process, stable for its lifetime. The pid keeps it readable
#: in the table when debugging which instance holds a duty.
INSTANCE_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

#: Duty names.
SCHEDULER = "workflow-scheduler"


async def acquire(
    db: aiosqlite.Connection, name: str, ttl_seconds: int, owner: str | None = None
) -> bool:
    """Take or renew the lease on ``name``. True means this instance is leader.

    Safe to call every tick: a leader renews, a follower keeps returning False
    until the leader stops renewing.
    """
    owner = owner or INSTANCE_ID
    now = time.time()
    expires_at = int(now + ttl_seconds)

    # Fast path: claim a free name. INSERT OR IGNORE loses the race harmlessly.
    await db.execute(
        "INSERT OR IGNORE INTO instance_leases (name, owner, expires_at) VALUES (?, ?, ?)",
        (name, owner, expires_at),
    )
    cursor = await db.execute(
        "UPDATE instance_leases SET owner = ?, expires_at = ? "
        "WHERE name = ? AND (owner = ? OR expires_at <= ?)",
        (owner, expires_at, name, owner, now),
    )
    await db.commit()
    return cursor.rowcount > 0


async def release(db: aiosqlite.Connection, name: str, owner: str | None = None) -> None:
    """Give up the lease so another instance can take over immediately.

    Best-effort: a crash skips this and the lease simply expires.
    """
    await db.execute(
        "DELETE FROM instance_leases WHERE name = ? AND owner = ?",
        (name, owner or INSTANCE_ID),
    )
    await db.commit()


async def current_leader(db: aiosqlite.Connection, name: str) -> str | None:
    """Owner of the unexpired lease on ``name``, or None if nobody holds it."""
    async with db.execute(
        "SELECT owner FROM instance_leases WHERE name = ? AND expires_at > ?",
        (name, time.time()),
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None

"""
Single database-access layer — a small async connection pool over SQLite (WAL).

Historically every module opened its own ``aiosqlite.connect(settings.db_path)``
(12+ call sites), each paying the open + PRAGMA cost per request and each with
its own idea of connection setup. This module is the ONE place a connection is
created and configured, so:

* connections are reused (no per-call open/PRAGMA overhead);
* every connection is configured identically (``row_factory``, foreign keys,
  WAL-friendly PRAGMAs, sqlite-vec reload) — the exact behaviour the old
  ``database.get_db()`` / per-module ``_connect()`` helpers had;
* there is a single seam to swap SQLite for a networked DB (Postgres) later:
  the ``connection()`` / ``transaction()`` API stays, only ``_configure`` and
  the acquire/release internals change.

Behaviour is unchanged in single-node deployments: SQLite still has one writer,
but WAL lets pooled readers run concurrently and writers no longer serialise on
connection open. Acquire/release guarantees one task owns a connection at a
time, so the aiosqlite single-thread-per-connection model is never violated.

Usage::

    from app.db import pool

    async with pool.connection() as db:          # caller manages commits
        await db.execute(...)
        await db.commit()

    async with pool.transaction() as db:         # commit on success, rollback on error
        await db.execute(...)
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import aiosqlite

from app.core.config import settings

logger = logging.getLogger(__name__)


async def _configure(db: aiosqlite.Connection) -> None:
    """Apply the exact per-connection setup the old helpers used, plus WAL tuning.

    ``row_factory`` + ``PRAGMA foreign_keys=ON`` reproduce ``database.get_db()``
    byte-for-byte. ``busy_timeout`` makes a writer wait for the lock instead of
    failing fast with ``database is locked`` under concurrency (the main
    single-writer symptom called out in the roadmap). sqlite-vec is reloaded
    per connection so vec0 KNN queries resolve, mirroring ``get_db()``.
    """
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    # Wait up to 5s for the single writer lock rather than erroring immediately.
    await db.execute("PRAGMA busy_timeout=5000")

    # Reload sqlite-vec per connection (loadable extensions are per-connection).
    # Imported lazily to avoid a circular import with database.py at module load.
    from app.db import database

    if database._vec_available:
        await database._try_load_vec(db)


class PooledConnection:
    """Thin proxy over an ``aiosqlite.Connection`` whose ``close()`` releases.

    Lets the many existing ``db = await connect(...)`` / ``await db.close()``
    call sites migrate to the pool with a one-line change and no re-indentation:
    every attribute/method (``execute``, ``commit``, ``row_factory``,
    ``in_transaction``, ``async with db.execute(...)`` …) is delegated to the
    real connection, but ``close()`` returns it to the pool instead of tearing
    it down. Also usable as an async context manager (``async with``).
    """

    __slots__ = ("_conn", "_release", "_closed")

    def __init__(self, conn: aiosqlite.Connection, release) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_release", release)
        object.__setattr__(self, "_closed", False)

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(object.__getattribute__(self, "_conn"), name, value)

    async def close(self) -> None:
        if object.__getattribute__(self, "_closed"):
            return
        object.__setattr__(self, "_closed", True)
        await object.__getattribute__(self, "_release")(object.__getattribute__(self, "_conn"))

    async def __aenter__(self) -> "PooledConnection":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()


class _Pool:
    """Fixed-size pool of pre-configured aiosqlite connections.

    Connections are created lazily up to ``size`` and reused. ``acquire`` blocks
    on an ``asyncio.Queue`` when all connections are checked out, so the pool
    also bounds the number of concurrent SQLite handles.
    """

    def __init__(self, db_path: str, size: int) -> None:
        self._db_path = db_path
        self._size = max(1, size)
        self._idle: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue()
        self._created = 0
        self._lock = asyncio.Lock()
        self._closed = False

    async def _new_connection(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self._db_path)
        await _configure(db)
        return db

    async def acquire(self) -> aiosqlite.Connection:
        if self._closed:
            raise RuntimeError("connection pool is closed")
        # Fast path: hand back an idle connection if one is available.
        if not self._idle.empty():
            return self._idle.get_nowait()
        # Grow the pool up to its size before blocking on an in-use connection.
        async with self._lock:
            if self._created < self._size:
                self._created += 1
                try:
                    return await self._new_connection()
                except Exception:
                    self._created -= 1
                    raise
        return await self._idle.get()

    async def release(self, db: aiosqlite.Connection) -> None:
        """Return a connection to the pool, resetting any open transaction.

        The old ``get_db()`` simply ``close()``d the connection, which rolls
        back any uncommitted work. A pooled connection is reused, so we roll
        back explicitly to guarantee the next borrower sees a clean session —
        no transaction state leaks between callers.
        """
        if self._closed:
            await db.close()
            return
        try:
            if db.in_transaction:
                await db.rollback()
        except Exception:
            # A broken connection must not be returned to the pool.
            logger.warning("Discarding DB connection after failed rollback", exc_info=True)
            self._created -= 1
            try:
                await db.close()
            finally:
                await self._backfill()
            return
        self._idle.put_nowait(db)

    async def _backfill(self) -> None:
        """Recreate a connection dropped by a failed rollback, best-effort."""
        try:
            async with self._lock:
                if self._created < self._size and not self._closed:
                    self._created += 1
                    conn = await self._new_connection()
                    self._idle.put_nowait(conn)
        except Exception:
            self._created -= 1
            logger.warning("Failed to backfill DB pool connection", exc_info=True)

    async def close(self) -> None:
        self._closed = True
        while not self._idle.empty():
            conn = self._idle.get_nowait()
            try:
                await conn.close()
            except Exception:  # noqa: BLE001 — shutdown: a failed close is moot
                logger.debug("Error closing pooled connection at shutdown", exc_info=True)
        self._created = 0


_pool: _Pool | None = None


async def init_pool() -> None:
    """Create the process-wide pool. Called once from the app lifespan startup."""
    global _pool
    if _pool is not None:
        return
    _pool = _Pool(settings.db_path, settings.db_pool_size)
    logger.info("DB pool initialised (path=%s, size=%d)", settings.db_path, settings.db_pool_size)


async def close_pool() -> None:
    """Close every pooled connection. Called from the app lifespan shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _release_direct(conn: aiosqlite.Connection) -> None:
    await conn.close()


async def checkout() -> PooledConnection:
    """Borrow a connection as a ``PooledConnection`` handle.

    Drop-in for ``db = await aiosqlite.connect(settings.db_path)`` when the code
    already ends with ``await db.close()``: swap the two lines and the existing
    ``close()`` releases to the pool instead. Falls back to a one-shot direct
    connection (that ``close()`` really closes) when the pool isn't initialised.
    """
    if _pool is None:
        conn = await aiosqlite.connect(settings.db_path)
        await _configure(conn)
        return PooledConnection(conn, _release_direct)
    conn = await _pool.acquire()
    return PooledConnection(conn, _pool.release)


@asynccontextmanager
async def connection():
    """Borrow a configured connection; the caller manages commits.

    Drop-in replacement for the old ``db = await aiosqlite.connect(...)`` /
    ``try: ... finally: await db.close()`` blocks. On exit the connection is
    returned to the pool (any open transaction is rolled back).

    When the pool isn't initialised (standalone scripts / tests that skip the
    app lifespan) it falls back to a one-shot direct connection, so migrated
    call sites behave exactly as before in those contexts.
    """
    if _pool is None:
        db = await aiosqlite.connect(settings.db_path)
        await _configure(db)
        try:
            yield db
        finally:
            await db.close()
        return

    db = await _pool.acquire()
    try:
        yield db
    finally:
        await _pool.release(db)


@asynccontextmanager
async def transaction():
    """Borrow a connection and commit on success / rollback on error.

    Use when a block should be atomic and the caller would otherwise end with
    an explicit ``await db.commit()``. Same pool-less fallback as ``connection``.
    """
    async with connection() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

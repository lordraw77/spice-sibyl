"""Versioned schema migrations (roadmap v2 § 3, P1).

Covers the ledger itself, not the historical statements: that every migration
is recorded once, that a second boot is a no-op, that a broken migration stops
the boot instead of half-applying, and that the legacy baseline still tolerates
statements a pre-ledger database already ran.
"""

import asyncio

import aiosqlite
import pytest

from app.db import migrations


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture()
def db(tmp_path):
    """A throwaway database with just the ledger — no app schema needed."""
    path = tmp_path / "migrations.db"

    async def _open():
        conn = await aiosqlite.connect(path)
        await conn.execute("CREATE TABLE demo (id TEXT PRIMARY KEY)")
        await conn.commit()
        return conn

    conn = _run(_open())
    yield conn
    _run(conn.close())


def _migration(version, name, *statements, tolerant=False):
    return migrations.Migration(
        version=version, name=name, statements=tuple(statements), tolerant=tolerant
    )


def test_declared_migrations_have_unique_increasing_versions():
    versions = [m.version for m in migrations.MIGRATIONS]
    assert versions == sorted(set(versions)), "versions must be unique and ordered"
    assert versions[0] == 1


def test_apply_records_each_migration_once(db, monkeypatch):
    monkeypatch.setattr(
        migrations, "MIGRATIONS",
        (_migration(1, "add-a", "ALTER TABLE demo ADD COLUMN a TEXT"),
         _migration(2, "add-b", "ALTER TABLE demo ADD COLUMN b TEXT")),
    )

    assert _run(migrations.apply(db)) == [1, 2]

    async def _check():
        async with db.execute("SELECT version, name FROM schema_migrations ORDER BY version") as cur:
            return await cur.fetchall()

    assert [tuple(r) for r in _run(_check())] == [(1, "add-a"), (2, "add-b")]


def test_second_apply_is_a_no_op(db, monkeypatch):
    """The whole point: a boot must not replay what the database already has."""
    monkeypatch.setattr(
        migrations, "MIGRATIONS",
        (_migration(1, "add-a", "ALTER TABLE demo ADD COLUMN a TEXT"),),
    )
    assert _run(migrations.apply(db)) == [1]
    # A replay would raise "duplicate column name: a" — it is not tolerant.
    assert _run(migrations.apply(db)) == []


def test_only_the_new_migration_runs_on_an_existing_database(db, monkeypatch):
    monkeypatch.setattr(
        migrations, "MIGRATIONS",
        (_migration(1, "add-a", "ALTER TABLE demo ADD COLUMN a TEXT"),),
    )
    _run(migrations.apply(db))

    monkeypatch.setattr(
        migrations, "MIGRATIONS",
        (_migration(1, "add-a", "ALTER TABLE demo ADD COLUMN a TEXT"),
         _migration(2, "add-b", "ALTER TABLE demo ADD COLUMN b TEXT")),
    )
    assert _run(migrations.apply(db)) == [2]


def test_a_broken_migration_raises_and_is_not_recorded(db, monkeypatch):
    """Fail fast: a bad deploy stops at boot rather than stamping a lie."""
    monkeypatch.setattr(
        migrations, "MIGRATIONS",
        (_migration(1, "bogus", "ALTER TABLE does_not_exist ADD COLUMN a TEXT"),),
    )
    with pytest.raises(aiosqlite.OperationalError):
        _run(migrations.apply(db))
    assert _run(migrations.applied_versions(db)) == set()


def test_tolerant_migration_survives_an_already_applied_statement(db, monkeypatch):
    """How a pre-ledger database catches up: the statements may already be there."""
    _run(db.execute("ALTER TABLE demo ADD COLUMN a TEXT"))
    _run(db.commit())

    monkeypatch.setattr(
        migrations, "MIGRATIONS",
        (_migration(1, "legacy", "ALTER TABLE demo ADD COLUMN a TEXT",
                    "ALTER TABLE demo ADD COLUMN b TEXT", tolerant=True),),
    )
    assert _run(migrations.apply(db)) == [1]

    async def _columns():
        async with db.execute("PRAGMA table_info(demo)") as cur:
            return {row[1] for row in await cur.fetchall()}

    # The statement after the tolerated failure still ran.
    assert {"a", "b"} <= _run(_columns())


def test_pending_lists_what_is_missing(db, monkeypatch):
    monkeypatch.setattr(
        migrations, "MIGRATIONS",
        (_migration(1, "add-a", "ALTER TABLE demo ADD COLUMN a TEXT"),
         _migration(2, "add-b", "ALTER TABLE demo ADD COLUMN b TEXT")),
    )
    assert [m.version for m in _run(migrations.pending(db))] == [1, 2]
    _run(migrations.apply(db))
    assert _run(migrations.pending(db)) == []


def test_real_boot_stamps_the_legacy_baseline(_init_database):
    """init_db() ran against the suite's database: the ledger must be filled."""
    from app.core.config import settings

    async def _check():
        async with aiosqlite.connect(settings.db_path) as conn:
            return await migrations.applied_versions(conn)

    assert {m.version for m in migrations.MIGRATIONS} <= _run(_check())

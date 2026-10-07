"""Async database access layer.

SQLite via aiosqlite today. The whole codebase talks to the DB ONLY
through ``Database`` + the repository classes in ``repo.py`` — no raw
SQL in handlers. To move to Postgres later, rewrite ``Database`` (on
asyncpg) and ``repo.py``; handlers stay untouched.
"""
from __future__ import annotations

import pathlib
from typing import Any, Iterable, Optional, Sequence

import aiosqlite


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    # Columns added after the initial release. Applied idempotently on
    # every boot so existing installs pick them up without a dump/restore.
    _MIGRATIONS: tuple[tuple[str, str], ...] = (
        ("deals", "payable_pkr INTEGER NOT NULL DEFAULT 0"),
        ("deals", "commission_pkr INTEGER NOT NULL DEFAULT 0"),
        ("deals", "expected_usdt REAL NOT NULL DEFAULT 0"),
        ("deals", "pay_poll_started_at TEXT"),
        ("deals", "auto_verified INTEGER NOT NULL DEFAULT 0"),
    )

    async def init(self, schema_path: str) -> None:
        pathlib.Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        with open(schema_path, "r", encoding="utf-8") as fh:
            await self._conn.executescript(fh.read())
        await self._conn.commit()
        await self._migrate()

    async def _migrate(self) -> None:
        for table, column_ddl in self._MIGRATIONS:
            try:
                await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_ddl}")
            except Exception as exc:
                # "duplicate column name" = already migrated; anything else
                # is unexpected but must not break boot.
                if "duplicate column name" not in str(exc).lower():
                    raise
        await self._conn.commit()

    def _c(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database.init() was not called"
        return self._conn

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Cursor:
        cur = await self._c().execute(sql, params)
        await self._c().commit()
        return cur

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        await self._c().executemany(sql, seq)
        await self._c().commit()

    async def fetchone(self, sql: str, params: Sequence[Any] = ()):
        cur = await self._c().execute(sql, params)
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: Sequence[Any] = ()):
        cur = await self._c().execute(sql, params)
        return await cur.fetchall()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

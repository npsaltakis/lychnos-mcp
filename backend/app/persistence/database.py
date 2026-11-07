"""SQLite connection and schema migrations."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

# Append-only list of migrations; index + 1 == schema version.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE devices (
        id            TEXT PRIMARY KEY,
        provider      TEXT NOT NULL,
        native_id     TEXT NOT NULL,
        name          TEXT NOT NULL,
        manufacturer  TEXT NOT NULL,
        model         TEXT,
        address       TEXT,
        registered    INTEGER NOT NULL DEFAULT 0,
        capabilities  TEXT NOT NULL,
        metadata      TEXT NOT NULL DEFAULT '{}',
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        UNIQUE (provider, native_id)
    );
    CREATE TABLE scenes (
        id           TEXT PRIMARY KEY,
        name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description  TEXT NOT NULL DEFAULT '',
        actions      TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE sessions (
        id_hash       TEXT PRIMARY KEY,
        created_at    TEXT NOT NULL,
        expires_at    TEXT NOT NULL,
        user_agent    TEXT NOT NULL DEFAULT ''
    );
    """,
]


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA user_version") as cursor:
            row = await cursor.fetchone()
        current = int(row[0]) if row else 0
        for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
            log.info("applying migration", extra={"ctx": {"version": version}})
            await self.conn.executescript(script)
            await self.conn.execute(f"PRAGMA user_version = {version}")
            await self.conn.commit()

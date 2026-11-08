"""Daily copy of the database (devices, rooms, scenes, automations, log, settings).

A consistent snapshot is taken with SQLite's online backup while the backend keeps running.
The newest KEEP copies are kept in ``<data dir>/backups``. Secrets are not in the database
(they live in the OS keyring), so a backup holds no passwords or tokens.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
from pydantic import BaseModel

from app.persistence.database import Database

log = logging.getLogger(__name__)

KEEP = 14
EVERY = timedelta(hours=24)
CHECK_S = 3600.0
PREFIX = "lychnos-"


class BackupFile(BaseModel):
    name: str
    size_bytes: int
    created_at: datetime


class BackupService:
    def __init__(self, db: Database, folder: Path, *, keep: int = KEEP, check_s: float = CHECK_S) -> None:
        self._db = db
        self._folder = folder
        self._keep = keep
        self._check_s = check_s
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def folder(self) -> Path:
        return self._folder

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="backup")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def list(self) -> list[BackupFile]:
        if not self._folder.exists():
            return []
        files = sorted(self._folder.glob(f"{PREFIX}*.db"), key=lambda f: (f.stat().st_mtime, f.name), reverse=True)
        return [
            BackupFile(
                name=f.name,
                size_bytes=f.stat().st_size,
                created_at=datetime.fromtimestamp(f.stat().st_mtime).astimezone(),
            )
            for f in files
        ]

    async def create(self) -> BackupFile:
        async with self._lock:
            self._folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            target_path = self._folder / f"{PREFIX}{stamp}.db"
            n = 1
            while target_path.exists():  # two backups in the same second
                n += 1
                target_path = self._folder / f"{PREFIX}{stamp}-{n}.db"
            async with aiosqlite.connect(target_path) as target:
                await self._db.conn.backup(target)
            self._prune()
            log.info("database backup created", extra={"ctx": {"file": target_path.name}})
            return self.list()[0]

    def _prune(self) -> None:
        for old in sorted(self._folder.glob(f"{PREFIX}*.db"), key=lambda f: (f.stat().st_mtime, f.name), reverse=True)[self._keep :]:
            old.unlink(missing_ok=True)

    def _due(self) -> bool:
        latest = self.list()
        return not latest or datetime.now().astimezone() - latest[0].created_at >= EVERY

    async def _loop(self) -> None:
        while True:
            try:
                if self._due():
                    await self.create()
            except Exception:
                log.exception("database backup failed")
            await asyncio.sleep(self._check_s)

"""Repositories mapping domain models to SQLite rows."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import aiosqlite

from app.core.errors import ConflictError
from app.domain.models import Capabilities, DeviceRecord, Scene, SceneAction
from app.persistence.database import Database


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class DeviceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self) -> list[DeviceRecord]:
        async with self._db.conn.execute("SELECT * FROM devices ORDER BY name") as cursor:
            rows = await cursor.fetchall()
        return [self._from_row(row) for row in rows]

    async def upsert(self, device: DeviceRecord) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO devices (id, provider, native_id, name, manufacturer, model, address,
                                 registered, capabilities, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, manufacturer=excluded.manufacturer, model=excluded.model,
                address=excluded.address, registered=excluded.registered,
                capabilities=excluded.capabilities, metadata=excluded.metadata,
                updated_at=excluded.updated_at
            """,
            (
                device.id,
                device.provider,
                device.native_id,
                device.name,
                device.manufacturer,
                device.model,
                device.address,
                int(device.registered),
                device.capabilities.model_dump_json(),
                json.dumps(device.metadata),
                device.created_at.isoformat(),
                device.updated_at.isoformat(),
            ),
        )
        await self._db.conn.commit()

    async def delete(self, device_id: str) -> None:
        await self._db.conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        await self._db.conn.commit()

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> DeviceRecord:
        return DeviceRecord(
            id=row["id"],
            provider=row["provider"],
            native_id=row["native_id"],
            name=row["name"],
            manufacturer=row["manufacturer"],
            model=row["model"],
            address=row["address"],
            registered=bool(row["registered"]),
            capabilities=Capabilities.model_validate_json(row["capabilities"]),
            metadata=json.loads(row["metadata"]),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )


class SceneRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self) -> list[Scene]:
        async with self._db.conn.execute("SELECT * FROM scenes ORDER BY name") as cursor:
            rows = await cursor.fetchall()
        return [self._from_row(row) for row in rows]

    async def get(self, scene_id: str) -> Scene | None:
        async with self._db.conn.execute("SELECT * FROM scenes WHERE id = ?", (scene_id,)) as cursor:
            row = await cursor.fetchone()
        return self._from_row(row) if row else None

    async def save(self, scene: Scene) -> None:
        try:
            await self._db.conn.execute(
                """
                INSERT INTO scenes (id, name, description, actions, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, description=excluded.description,
                    actions=excluded.actions, updated_at=excluded.updated_at
                """,
                (
                    scene.id,
                    scene.name,
                    scene.description,
                    json.dumps([a.model_dump(mode="json") for a in scene.actions]),
                    scene.created_at.isoformat(),
                    scene.updated_at.isoformat(),
                ),
            )
            await self._db.conn.commit()
        except sqlite3.IntegrityError as exc:
            await self._db.conn.rollback()
            raise ConflictError(
                f"A scene named {scene.name!r} already exists", details={"name": scene.name}
            ) from exc

    async def delete(self, scene_id: str) -> bool:
        cursor = await self._db.conn.execute("DELETE FROM scenes WHERE id = ?", (scene_id,))
        await self._db.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> Scene:
        return Scene(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            actions=[SceneAction.model_validate(a) for a in json.loads(row["actions"])],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

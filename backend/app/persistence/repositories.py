"""Repositories mapping domain models to SQLite rows."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

from app.core.errors import ConflictError
from app.domain.automations import Automation
from app.domain.models import ActivityEntry, Capabilities, DeviceRecord, Room, Scene, SceneAction, Weighing
from app.persistence.database import Database

ACTIVITY_KEEP = 5000


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
                                 registered, room_id, capabilities, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, manufacturer=excluded.manufacturer, model=excluded.model,
                address=excluded.address, registered=excluded.registered, room_id=excluded.room_id,
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
                device.room_id,
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
            room_id=row["room_id"],
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


class RoomRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self) -> list[Room]:
        async with self._db.conn.execute("SELECT * FROM rooms ORDER BY sort_order, name") as cursor:
            rows = await cursor.fetchall()
        return [
            Room(id=r["id"], name=r["name"], icon=r["icon"], sort_order=r["sort_order"], created_at=_dt(r["created_at"]))
            for r in rows
        ]

    async def save(self, room: Room) -> None:
        try:
            await self._db.conn.execute(
                """
                INSERT INTO rooms (id, name, icon, sort_order, created_at) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, icon=excluded.icon, sort_order=excluded.sort_order
                """,
                (room.id, room.name, room.icon, room.sort_order, room.created_at.isoformat()),
            )
            await self._db.conn.commit()
        except sqlite3.IntegrityError as exc:
            await self._db.conn.rollback()
            raise ConflictError(f"A room named {room.name!r} already exists", details={"name": room.name}) from exc

    async def delete(self, room_id: str) -> bool:
        await self._db.conn.execute("UPDATE devices SET room_id = NULL WHERE room_id = ?", (room_id,))
        cursor = await self._db.conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        await self._db.conn.commit()
        return cursor.rowcount > 0


class AutomationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self) -> list[Automation]:
        async with self._db.conn.execute("SELECT * FROM automations ORDER BY name") as cursor:
            rows = await cursor.fetchall()
        return [self._from_row(r) for r in rows]

    async def save(self, automation: Automation) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO automations (id, name, enabled, trigger, action, last_run_at, last_result, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, enabled=excluded.enabled, trigger=excluded.trigger, action=excluded.action,
                last_run_at=excluded.last_run_at, last_result=excluded.last_result, updated_at=excluded.updated_at
            """,
            (
                automation.id,
                automation.name,
                int(automation.enabled),
                automation.trigger.model_dump_json(),
                automation.action.model_dump_json(),
                automation.last_run_at.isoformat() if automation.last_run_at else None,
                json.dumps(automation.last_result) if automation.last_result is not None else None,
                automation.created_at.isoformat(),
                automation.updated_at.isoformat(),
            ),
        )
        await self._db.conn.commit()

    async def delete(self, automation_id: str) -> bool:
        cursor = await self._db.conn.execute("DELETE FROM automations WHERE id = ?", (automation_id,))
        await self._db.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> Automation:
        return Automation.model_validate(
            {
                "id": row["id"],
                "name": row["name"],
                "enabled": bool(row["enabled"]),
                "trigger": json.loads(row["trigger"]),
                "action": json.loads(row["action"]),
                "last_run_at": row["last_run_at"],
                "last_result": json.loads(row["last_result"]) if row["last_result"] else None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )


class ActivityRepository:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._writes = 0

    async def add(
        self, *, ts: datetime, source: str, device_id: str | None, device_name: str | None,
        command: str, outcome: str, detail: str | None = None,
    ) -> None:
        await self._db.conn.execute(
            "INSERT INTO activity (ts, source, device_id, device_name, command, outcome, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts.isoformat(), source, device_id, device_name, command, outcome, detail),
        )
        self._writes += 1
        if self._writes % 200 == 0:  # keep the log bounded
            await self._db.conn.execute(
                "DELETE FROM activity WHERE id <= (SELECT MAX(id) FROM activity) - ?", (ACTIVITY_KEEP,)
            )
        await self._db.conn.commit()

    async def recent(
        self, limit: int = 100, device_id: str | None = None, kind: str | None = None
    ) -> list[ActivityEntry]:
        """kind: 'commands' (sent by Lychnos), 'changes' (made elsewhere), 'availability'."""
        query = "SELECT * FROM activity"
        where: list[str] = []
        params: tuple[object, ...] = ()
        if device_id:
            where.append("device_id = ?")
            params = (*params, device_id)
        if kind == "changes":
            where.append("outcome = 'observed'")
        elif kind == "availability":
            where.append("outcome IN ('offline', 'online')")
        elif kind == "commands":
            where.append("outcome NOT IN ('observed', 'offline', 'online')")
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY id DESC LIMIT ?"
        async with self._db.conn.execute(query, (*params, limit)) as cursor:
            rows = await cursor.fetchall()
        return [
            ActivityEntry(
                id=r["id"], ts=_dt(r["ts"]), source=r["source"], device_id=r["device_id"],
                device_name=r["device_name"], command=r["command"], outcome=r["outcome"], detail=r["detail"],
            )
            for r in rows
        ]


class PowerEventRepository:
    """On/off moments of each light, for usage statistics. Kept for KEEP_DAYS."""

    KEEP_DAYS = 400

    def __init__(self, db: Database) -> None:
        self._db = db
        self._writes = 0

    async def add(self, device_id: str, ts: datetime, is_on: bool) -> None:
        await self._db.conn.execute(
            "INSERT INTO power_events (device_id, ts, is_on) VALUES (?, ?, ?)", (device_id, ts.isoformat(), int(is_on))
        )
        self._writes += 1
        if self._writes % 500 == 0:
            cutoff = (ts - timedelta(days=self.KEEP_DAYS)).isoformat()
            await self._db.conn.execute("DELETE FROM power_events WHERE ts < ?", (cutoff,))
        await self._db.conn.commit()

    async def between(self, since: datetime, until: datetime) -> dict[str, list[tuple[datetime, bool]]]:
        """Events per device in [since, until], plus each device's last event before ``since``."""
        out: dict[str, list[tuple[datetime, bool]]] = {}
        async with self._db.conn.execute(
            """
            SELECT device_id, ts, is_on FROM power_events p
            WHERE ts = (SELECT MAX(ts) FROM power_events q WHERE q.device_id = p.device_id AND q.ts < ?)
            UNION ALL
            SELECT device_id, ts, is_on FROM power_events WHERE ts >= ? AND ts <= ?
            ORDER BY device_id, ts
            """,
            (since.isoformat(), since.isoformat(), until.isoformat()),
        ) as cursor:
            for row in await cursor.fetchall():
                out.setdefault(row["device_id"], []).append((_dt(row["ts"]), bool(row["is_on"])))
        return out


class WeighingRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _row(row: Any) -> Weighing:
        return Weighing(id=row["id"], ts=_dt(row["ts"]), kg=row["kg"])

    async def add(self, ts: datetime, kg: float) -> Weighing:
        cursor = await self._db.conn.execute("INSERT INTO weighings (ts, kg) VALUES (?, ?)", (ts.isoformat(), kg))
        await self._db.conn.commit()
        return Weighing(id=int(cursor.lastrowid or 0), ts=ts, kg=kg)

    async def since(self, since: datetime) -> list[Weighing]:
        async with self._db.conn.execute(
            "SELECT id, ts, kg FROM weighings WHERE ts >= ? ORDER BY ts", (since.isoformat(),)
        ) as cursor:
            return [self._row(r) for r in await cursor.fetchall()]

    async def latest(self) -> Weighing | None:
        async with self._db.conn.execute("SELECT id, ts, kg FROM weighings ORDER BY ts DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
        return self._row(row) if row else None

    async def delete(self, weighing_id: int) -> bool:
        cursor = await self._db.conn.execute("DELETE FROM weighings WHERE id = ?", (weighing_id,))
        await self._db.conn.commit()
        return cursor.rowcount > 0


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> object | None:
        async with self._db.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
        return json.loads(row["value"]) if row else None

    async def set(self, key: str, value: object) -> None:
        await self._db.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        await self._db.conn.commit()

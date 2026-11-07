"""Rooms: named groups of devices, controllable together."""

from __future__ import annotations

import uuid

from app.core.errors import ConflictError, NotFoundError
from app.domain.models import BatchResult, Command, DeviceView, Room, RoomInput
from app.persistence.repositories import RoomRepository
from app.services.device_service import DeviceService
from app.services.events import EventBus, EventType


class RoomService:
    def __init__(self, repo: RoomRepository, devices: DeviceService, bus: EventBus) -> None:
        self._repo = repo
        self._devices = devices
        self._bus = bus

    async def list(self) -> list[Room]:
        return await self._repo.list()

    async def get(self, room_id: str) -> Room:
        for room in await self._repo.list():
            if room.id == room_id:
                return room
        raise NotFoundError(f"Unknown room {room_id!r}", details={"room_id": room_id})

    async def create(self, data: RoomInput) -> Room:
        rooms = await self._repo.list()
        self._check_unique(rooms, data.name, None)
        room = Room(id=uuid.uuid4().hex[:12], sort_order=len(rooms), **data.model_dump())
        await self._repo.save(room)
        self._bus.publish(EventType.ROOMS_CHANGED)
        return room

    async def update(self, room_id: str, data: RoomInput) -> Room:
        self._check_unique(await self._repo.list(), data.name, room_id)
        room = (await self.get(room_id)).model_copy(update=data.model_dump())
        await self._repo.save(room)
        self._bus.publish(EventType.ROOMS_CHANGED)
        return room

    async def delete(self, room_id: str) -> None:
        await self.get(room_id)
        await self._repo.delete(room_id)
        self._devices.room_removed(room_id)
        self._bus.publish(EventType.ROOMS_CHANGED)

    async def assign(self, device_id: str, room_id: str | None) -> DeviceView:
        if room_id is not None:
            await self.get(room_id)
        return await self._devices.update_device(device_id, room_id=room_id)

    @staticmethod
    def _check_unique(rooms: list[Room], name: str, own_id: str | None) -> None:
        # SQLite's NOCASE only folds ASCII; compare Unicode-aware here (Σαλόνι == σαλόνι).
        wanted = name.strip().casefold()
        if any(r.name.casefold() == wanted and r.id != own_id for r in rooms):
            raise ConflictError(f"A room named {name!r} already exists", details={"name": name})

    async def command(self, room_id: str, command: Command, *, source: str = "api") -> BatchResult:
        await self.get(room_id)
        return await self._devices.execute_on_group(command, room_id=room_id, source=source)

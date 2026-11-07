"""Scenes: named sets of desired device states, applied through the DeviceService."""

from __future__ import annotations

import asyncio
import uuid

from app.core.errors import AppError, InvalidCommandError, NotFoundError
from app.domain.models import (
    BatchResult,
    CommandResult,
    Scene,
    SceneInput,
    SkippedDevice,
    utcnow,
)
from app.persistence.repositories import SceneRepository
from app.services.device_service import DeviceService
from app.services.events import EventBus, EventType


class SceneService:
    def __init__(self, repo: SceneRepository, devices: DeviceService, bus: EventBus) -> None:
        self._repo = repo
        self._devices = devices
        self._bus = bus

    async def list(self) -> list[Scene]:
        return await self._repo.list()

    async def get(self, scene_id: str) -> Scene:
        scene = await self._repo.get(scene_id)
        if scene is None:
            raise NotFoundError(f"Unknown scene {scene_id!r}", details={"scene_id": scene_id})
        return scene

    async def create(self, data: SceneInput) -> Scene:
        self._validate(data)
        scene = Scene(id=uuid.uuid4().hex[:12], **data.model_dump())
        await self._repo.save(scene)
        self._bus.publish(EventType.SCENES_CHANGED)
        return scene

    async def update(self, scene_id: str, data: SceneInput) -> Scene:
        existing = await self.get(scene_id)
        self._validate(data)
        scene = Scene(
            id=existing.id, created_at=existing.created_at, updated_at=utcnow(), **data.model_dump()
        )
        await self._repo.save(scene)
        self._bus.publish(EventType.SCENES_CHANGED)
        return scene

    async def delete(self, scene_id: str) -> None:
        if not await self._repo.delete(scene_id):
            raise NotFoundError(f"Unknown scene {scene_id!r}", details={"scene_id": scene_id})
        self._bus.publish(EventType.SCENES_CHANGED)

    async def activate(self, scene_id: str) -> BatchResult:
        """Apply a scene. Devices run in parallel; commands per device run in order."""
        scene = await self.get(scene_id)
        runnable: list[tuple[str, list]] = []
        skipped: list[SkippedDevice] = []
        for action in scene.actions:
            try:
                device = self._devices.get_device(action.device_id)
                commands = action.to_commands()
                for command in commands:
                    self._devices.validate_command(device, command)
            except AppError as error:
                skipped.append(SkippedDevice(device_id=action.device_id, reason=error.message))
                continue
            runnable.append((action.device_id, commands))

        per_device = await asyncio.gather(
            *(self._devices.execute_sequence(device_id, cmds) for device_id, cmds in runnable)
        )
        results: list[CommandResult] = [r for device_results in per_device for r in device_results]
        return BatchResult.build(results, skipped)

    def _validate(self, data: SceneInput) -> None:
        problems: list[dict[str, str]] = []
        for action in data.actions:
            try:
                device = self._devices.get_device(action.device_id)
                for command in action.to_commands():
                    self._devices.validate_command(device, command)
            except AppError as error:
                problems.append({"device_id": action.device_id, "error": error.message})
        if problems:
            raise InvalidCommandError(
                "The scene contains actions that cannot be applied", details={"problems": problems}
            )

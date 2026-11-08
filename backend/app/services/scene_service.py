"""Scenes: named sets of desired device states, applied through the DeviceService."""

from __future__ import annotations

import asyncio
import uuid

from app.core.errors import AppError, ConflictError, InvalidCommandError, NotFoundError
from app.domain.models import (
    Capability,
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
        self.effects = None  # EffectService, set at start-up (for scenes that start an effect)

    async def list(self) -> list[Scene]:
        return await self._repo.list()

    async def get(self, scene_id: str) -> Scene:
        scene = await self._repo.get(scene_id)
        if scene is None:
            raise NotFoundError(f"Unknown scene {scene_id!r}", details={"scene_id": scene_id})
        return scene

    async def _check_unique(self, name: str, own_id: str | None) -> None:
        # SQLite's NOCASE only folds ASCII; compare Unicode-aware here (Ταινία == ταινία).
        wanted = name.strip().casefold()
        if any(s.name.casefold() == wanted and s.id != own_id for s in await self._repo.list()):
            raise ConflictError(f"A scene named {name!r} already exists", details={"name": name})

    async def create(self, data: SceneInput) -> Scene:
        self._validate(data)
        await self._check_unique(data.name, None)
        scene = Scene(id=uuid.uuid4().hex[:12], **data.model_dump())
        await self._repo.save(scene)
        self._bus.publish(EventType.SCENES_CHANGED)
        return scene

    async def update(self, scene_id: str, data: SceneInput) -> Scene:
        existing = await self.get(scene_id)
        self._validate(data)
        await self._check_unique(data.name, scene_id)
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

    async def activate(self, scene_id: str, *, source: str = "api") -> BatchResult:
        """Apply a scene. Devices run in parallel; commands per device run in order."""
        scene = await self.get(scene_id)
        source = f"{source} · scene {scene.name}"
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
            *(self._devices.execute_sequence(device_id, cmds, source=source) for device_id, cmds in runnable)
        )
        results: list[CommandResult] = [r for device_results in per_device for r in device_results]
        runnable_ids = {device_id for device_id, _ in runnable}
        for action in scene.actions:
            if action.device_id in runnable_ids:
                try:
                    await self._apply_extra(action, source)
                except AppError as error:
                    skipped.append(SkippedDevice(device_id=action.device_id, reason=error.message))
        return BatchResult.build(results, skipped)

    async def _apply_extra(self, action, source: str) -> None:
        """Gradient, effect or music after the plain values (they need the light on)."""
        if action.gradient:
            await self._devices.apply_gradient(
                action.device_id, action.gradient, per_bar=action.gradient_per_bar, source=source
            )
        elif action.effect and self.effects is not None:
            from app.domain.automations import Target
            from app.services.effect_service import EffectRequest

            await self.effects.start(
                EffectRequest(effect=action.effect, target=Target(kind="device", id=action.device_id)), source=source
            )
        elif action.music_style is not None:
            await self._devices.set_music(action.device_id, action.music_style, action.music_sensitivity, source=source)

    def _validate(self, data: SceneInput) -> None:
        problems: list[dict[str, str]] = []
        for action in data.actions:
            try:
                device = self._devices.get_device(action.device_id)
                for command in action.to_commands():
                    self._devices.validate_command(device, command)
                if action.gradient and self._devices.segment_count(device.id) == 0:
                    raise InvalidCommandError(f"{device.name} has no colour segments for a gradient")
                if action.music_style is not None and not self._devices.supports_music(device.id):
                    raise InvalidCommandError(f"{device.name} has no music mode")
                if action.effect:
                    need = Capability.BRIGHTNESS if action.effect == "breathe" else Capability.COLOR_RGB
                    if not device.capabilities.has(need):
                        raise InvalidCommandError(f"{device.name} cannot show this effect")
            except AppError as error:
                problems.append({"device_id": action.device_id, "error": error.message})
        if problems:
            raise InvalidCommandError(
                "The scene contains actions that cannot be applied", details={"problems": problems}
            )

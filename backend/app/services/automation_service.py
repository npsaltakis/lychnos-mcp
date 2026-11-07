"""Automation engine: runs actions at a time of day or at sunrise/sunset.

Times are the computer's local time (with its daylight-saving rules). Runs that were missed
while the backend was not running are skipped, never replayed later.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.core.errors import AppError, InvalidCommandError, NotFoundError
from app.domain.automations import (
    Automation,
    AutomationInput,
    CommandActionSpec,
    FadeActionSpec,
    Location,
    SceneActionSpec,
    SunTrigger,
    Target,
    TimeTrigger,
    Trigger,
)
from app.domain.models import (
    BatchResult,
    BatchOutcome,
    Capability,
    Command,
    CommandOutcome,
    SetBrightness,
    SetColorTemperature,
    SetPower,
    utcnow,
)
from app.persistence.repositories import AutomationRepository, RoomRepository, SettingsRepository
from app.services.device_service import DeviceService
from app.services.events import EventBus, EventType
from app.services.scene_service import SceneService
from app.services.sun import sun_event

log = logging.getLogger(__name__)

LOCATION_KEY = "location"
TICK_S = 15.0
FADE_MIN_STEP_S = 15.0
_sleep = asyncio.sleep  # indirection so tests can run fades instantly


def next_run(trigger: Trigger, after: datetime, location: Location | None) -> datetime | None:
    """First moment strictly after ``after`` when the trigger fires (aware datetime), or None."""
    local_after = after.astimezone()
    for offset_days in range(0, 9):
        day = (local_after + timedelta(days=offset_days)).date()
        if day.weekday() not in trigger.days:
            continue
        if isinstance(trigger, TimeTrigger):
            hour, minute = (int(x) for x in trigger.time.split(":"))
            # A naive local time converted with .astimezone() uses the OS DST rules for that date.
            candidate = datetime(day.year, day.month, day.day, hour, minute).astimezone()
        else:
            if location is None:
                return None
            event = sun_event(day, location.latitude, location.longitude, trigger.event)
            if event is None:
                continue
            candidate = event + timedelta(minutes=trigger.offset_min)
        if candidate > after:
            return candidate
    return None


def summarize(batch: BatchResult) -> dict[str, Any]:
    confirmed = sum(1 for r in batch.results if r.outcome is CommandOutcome.CONFIRMED)
    failed = [r for r in batch.results if r.outcome is CommandOutcome.FAILED]
    return {
        "outcome": batch.outcome.value,
        "message": f"{confirmed}/{len(batch.results)} confirmed"
        + (f", {len(failed)} failed: {failed[0].error.message}" if failed and failed[0].error else "")
        + (f", {len(batch.skipped)} skipped" if batch.skipped else ""),
    }


class AutomationService:
    def __init__(
        self,
        repo: AutomationRepository,
        settings: SettingsRepository,
        rooms: RoomRepository,
        devices: DeviceService,
        scenes: SceneService,
        bus: EventBus,
        tick_s: float = TICK_S,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._rooms = rooms
        self._devices = devices
        self._scenes = scenes
        self._bus = bus
        self._tick_s = tick_s
        self._automations: dict[str, Automation] = {}
        self._next: dict[str, datetime | None] = {}
        self._location: Location | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._running: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        raw = await self._settings.get(LOCATION_KEY)
        self._location = Location.model_validate(raw) if raw else None
        for automation in await self._repo.list():
            self._automations[automation.id] = automation
        self._reschedule_all()
        self._loop_task = asyncio.create_task(self._loop(), name="automations")
        log.info("automations started", extra={"ctx": {"count": len(self._automations)}})

    async def stop(self) -> None:
        tasks = [t for t in [self._loop_task, *self._running.values()] if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------------ location

    def get_location(self) -> Location | None:
        return self._location

    async def set_location(self, location: Location) -> Location:
        await self._settings.set(LOCATION_KEY, location.model_dump())
        self._location = location
        self._reschedule_all()
        self._changed()
        return location

    # ------------------------------------------------------------------ CRUD

    def list(self) -> list[Automation]:
        return sorted((self._with_next(a) for a in self._automations.values()), key=lambda a: a.name.lower())

    def get(self, automation_id: str) -> Automation:
        automation = self._automations.get(automation_id)
        if automation is None:
            raise NotFoundError(f"Unknown automation {automation_id!r}", details={"automation_id": automation_id})
        return self._with_next(automation)

    async def create(self, data: AutomationInput) -> Automation:
        await self._validate(data)
        automation = Automation(id=uuid.uuid4().hex[:12], **data.model_dump())
        return await self._store(automation)

    async def update(self, automation_id: str, data: AutomationInput) -> Automation:
        existing = self.get(automation_id)
        await self._validate(data)
        automation = Automation(
            id=existing.id,
            last_run_at=existing.last_run_at,
            last_result=existing.last_result,
            created_at=existing.created_at,
            updated_at=utcnow(),
            **data.model_dump(),
        )
        return await self._store(automation)

    async def set_enabled(self, automation_id: str, enabled: bool) -> Automation:
        automation = self.get(automation_id).model_copy(update={"enabled": enabled, "updated_at": utcnow()})
        return await self._store(automation)

    async def delete(self, automation_id: str) -> None:
        self.get(automation_id)
        task = self._running.pop(automation_id, None)
        if task is not None:
            task.cancel()
        await self._repo.delete(automation_id)
        self._automations.pop(automation_id, None)
        self._next.pop(automation_id, None)
        self._changed()

    async def run_now(self, automation_id: str, *, wait: bool = True) -> Automation:
        """Run immediately. Fades run in the background (they can last minutes)."""
        automation = self.get(automation_id)
        if automation_id in self._running:
            raise InvalidCommandError(f"{automation.name} is already running")
        task = self._spawn(automation, reason="manual")
        if wait and not isinstance(automation.action, FadeActionSpec):
            await asyncio.shield(task)
        return self.get(automation_id)

    def _spawn(self, automation: Automation, *, reason: str) -> asyncio.Task[None]:
        task = asyncio.create_task(self._run(automation, reason=reason))
        self._running[automation.id] = task
        task.add_done_callback(lambda _t, aid=automation.id: self._running.pop(aid, None))
        return task

    # ------------------------------------------------------------------ scheduling

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._tick_s)
            try:
                await self._tick(utcnow())
            except Exception:
                log.exception("automation tick failed")

    async def _tick(self, now: datetime) -> None:
        for automation in list(self._automations.values()):
            due = self._next.get(automation.id)
            if not automation.enabled or due is None or now < due:
                continue
            self._next[automation.id] = next_run(automation.trigger, now + timedelta(seconds=1), self._location)
            if automation.id in self._running:
                log.info("automation still running, skipped", extra={"ctx": {"automation": automation.name}})
                continue
            self._spawn(automation, reason="schedule")

    def _reschedule_all(self) -> None:
        now = utcnow()
        for automation in self._automations.values():
            self._next[automation.id] = next_run(automation.trigger, now, self._location)

    def _with_next(self, automation: Automation) -> Automation:
        return automation.model_copy(update={"next_run_at": self._next.get(automation.id) if automation.enabled else None})

    # ------------------------------------------------------------------ running

    async def _run(self, automation: Automation, *, reason: str) -> None:
        source = f"automation · {automation.name}"
        log.info("automation running", extra={"ctx": {"automation": automation.name, "reason": reason}})
        action = automation.action
        try:
            if isinstance(action, SceneActionSpec):
                result = summarize(await self._scenes.activate(action.scene_id, source=source))
            elif isinstance(action, CommandActionSpec):
                result = await self._run_command(action.target, action.command, source)
            else:
                result = await self._run_fade(action, source)
        except AppError as error:
            result = {"outcome": "failed", "message": error.message}
        except asyncio.CancelledError:
            result = {"outcome": "failed", "message": "Cancelled"}
            await self._record(automation.id, result)
            raise
        except Exception as exc:
            log.exception("automation crashed", extra={"ctx": {"automation": automation.name}})
            result = {"outcome": "failed", "message": str(exc)}
        await self._record(automation.id, result)

    async def _run_command(self, target: Target, command: Command, source: str) -> dict[str, Any]:
        if target.kind == "device":
            assert target.id is not None
            result = await self._devices.execute(target.id, command, source=source)
            batch = BatchResult.build([result], [])
        else:
            batch = await self._devices.execute_on_group(
                command, room_id=target.id if target.kind == "room" else None, source=source
            )
        return summarize(batch)

    async def _run_fade(self, spec: FadeActionSpec, source: str) -> dict[str, Any]:
        """Wake-up light: on at the lowest brightness, then raise it gradually."""
        devices = [
            d for d in self._target_devices(spec.target) if d.capabilities.has(Capability.BRIGHTNESS)
        ]
        if not devices:
            return {"outcome": BatchOutcome.NOTHING_TO_DO.value, "message": "No dimmable lights in the target"}
        start = max(d.capabilities.brightness_min for d in devices)
        active: list[str] = []
        for device in devices:
            commands: list[Command] = [SetPower(on=True)]
            rng = device.capabilities.color_temp_range
            if spec.color_temp_k and rng and device.capabilities.has(Capability.COLOR_TEMP):
                commands.append(SetColorTemperature(kelvin=min(rng.max_k, max(rng.min_k, spec.color_temp_k))))
            commands.append(SetBrightness(brightness=start))
            results = await self._devices.execute_sequence(device.id, commands, source=source)
            if results and results[-1].outcome is not CommandOutcome.FAILED:
                active.append(device.id)
        if not active:
            return {"outcome": "failed", "message": "No light could be switched on"}

        span = spec.to_brightness - start
        steps = max(1, min(span, int(spec.duration_min * 60 // FADE_MIN_STEP_S)))
        interval = spec.duration_min * 60 / steps
        for step in range(1, steps + 1):
            await _sleep(interval)
            level = start + round(span * step / steps)
            for device_id in list(active):
                # Someone switched it off during the fade: leave it alone.
                if self._devices.get_view(device_id).runtime.state.power is False:
                    active.remove(device_id)
                    continue
                await self._devices.execute(device_id, SetBrightness(brightness=level), source=source)
            if not active:
                return {"outcome": "partial", "message": "Stopped: the lights were switched off during the fade"}
        return {"outcome": "all_confirmed", "message": f"Faded {len(active)} light(s) to {spec.to_brightness}%"}

    def _target_devices(self, target: Target) -> list[Any]:
        if target.kind == "device":
            assert target.id is not None
            device = self._devices.get_device(target.id)
            return [device] if device.registered else []
        return self._devices.devices_in(target.id if target.kind == "room" else None)

    async def _record(self, automation_id: str, result: dict[str, Any]) -> None:
        automation = self._automations.get(automation_id)
        if automation is None:
            return
        now = utcnow()
        updated = automation.model_copy(update={"last_run_at": now, "last_result": {**result, "at": now.isoformat()}})
        self._automations[automation_id] = updated
        await self._repo.save(updated)
        self._changed()
        self._bus.publish(EventType.AUTOMATION_RAN, {"id": automation_id, "name": automation.name, **result})

    # ------------------------------------------------------------------ helpers

    async def _store(self, automation: Automation) -> Automation:
        await self._repo.save(automation)
        self._automations[automation.id] = automation
        self._next[automation.id] = next_run(automation.trigger, utcnow(), self._location)
        self._changed()
        return self._with_next(automation)

    async def _validate(self, data: AutomationInput) -> None:
        if isinstance(data.trigger, SunTrigger) and self._location is None:
            raise InvalidCommandError("Set your location in Settings before using sunrise/sunset")
        action = data.action
        if isinstance(action, SceneActionSpec):
            await self._scenes.get(action.scene_id)
            return
        target = action.target
        if target.kind == "room":
            if not any(r.id == target.id for r in await self._rooms.list()):
                raise NotFoundError(f"Unknown room {target.id!r}", details={"room_id": target.id})
        elif target.kind == "device":
            assert target.id is not None
            device = self._devices.get_device(target.id)
            if isinstance(action, CommandActionSpec):
                self._devices.validate_command(device, action.command)
            elif not device.capabilities.has(Capability.BRIGHTNESS):
                raise InvalidCommandError(f"{device.name} cannot be dimmed")

    def _changed(self) -> None:
        self._bus.publish(EventType.AUTOMATIONS_CHANGED)

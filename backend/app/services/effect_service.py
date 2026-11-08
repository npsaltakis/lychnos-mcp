"""Light effects run by Lychnos itself, so they work on any colour light (Govee, Yeelight, …).

- rainbow: the colour slowly goes round the colour wheel
- breathe: the brightness rises and falls slowly, in the current colour
- candle:  warm light that flickers like a flame

An effect keeps running until it is stopped, or until that light gets any other command
(from the dashboard, Claude, a scene, an automation…). The individual steps are not written
to the change log; starting and stopping is.
"""

from __future__ import annotations

import asyncio
import colorsys
import logging
import math
import random
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.core.errors import InvalidCommandError
from app.domain.automations import Target
from app.domain.models import RGB, Capability, SetBrightness, SetColor, SetPower, utcnow
from app.persistence.repositories import ActivityRepository
from app.services.device_service import DeviceService
from app.services.events import EventBus, EventType

log = logging.getLogger(__name__)

SOURCE = "effect"
EffectName = Literal["rainbow", "breathe", "candle"]
_sleep = asyncio.sleep  # tests replace this


class EffectRequest(BaseModel):
    effect: EffectName
    target: Target


class RunningEffect(BaseModel):
    effect: EffectName
    device_ids: list[str]
    started_at: datetime


NEEDS = {"rainbow": Capability.COLOR_RGB, "candle": Capability.COLOR_RGB, "breathe": Capability.BRIGHTNESS}


class EffectService:
    def __init__(
        self, devices: DeviceService, bus: EventBus, activity: ActivityRepository | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._devices = devices
        self._bus = bus
        self._activity = activity
        self._rng = rng or random.Random()
        self._effect_of: dict[str, str] = {}  # device id -> run id
        self._runs: dict[str, tuple[RunningEffect, asyncio.Task[None]]] = {}
        devices.command_listeners.append(self._on_command)

    # ------------------------------------------------------------------ API

    def running(self) -> list[RunningEffect]:
        return [run for run, _ in self._runs.values()]

    async def start(self, request: EffectRequest, *, source: str = "api") -> RunningEffect:
        need = NEEDS[request.effect]
        target = request.target
        if target.kind == "device":
            assert target.id is not None
            devices = [self._devices.get_device(target.id)]
        else:
            devices = self._devices.devices_in(target.id if target.kind == "room" else None)
        devices = [d for d in devices if d.registered and d.capabilities.has(need)]
        if not devices:
            raise InvalidCommandError("None of these lights can show this effect")
        ids = [d.id for d in devices]
        await self.stop(ids, log_it=False)
        run = RunningEffect(effect=request.effect, device_ids=ids, started_at=utcnow())
        run_id = f"{request.effect}-{run.started_at.timestamp()}"
        for device_id in ids:
            self._effect_of[device_id] = run_id
        task = asyncio.create_task(self._run(run_id, run), name=f"effect-{run_id}")
        self._runs[run_id] = (run, task)
        await self._log(ids, f"effect {request.effect}", source)
        self._changed()
        return run

    async def stop(self, device_ids: list[str] | None = None, *, log_it: bool = True, source: str = "api") -> None:
        """Stop effects on these lights (all effects when None)."""
        stopped: list[str] = []
        for device_id in list(self._effect_of) if device_ids is None else device_ids:
            if self._detach(device_id):
                stopped.append(device_id)
        if stopped and log_it:
            await self._log(stopped, "effect stop", source)
        if stopped:
            self._changed()

    # ------------------------------------------------------------------ internals

    def _on_command(self, device_id: str, source: str) -> None:
        if source != SOURCE and self._detach(device_id):
            self._changed()

    def _detach(self, device_id: str) -> bool:
        run_id = self._effect_of.pop(device_id, None)
        if run_id is None:
            return False
        entry = self._runs.get(run_id)
        if entry is not None:
            run, task = entry
            if device_id in run.device_ids:
                run.device_ids.remove(device_id)
            if not run.device_ids:
                task.cancel()
                self._runs.pop(run_id, None)
        return True

    def _active(self, run_id: str, run: RunningEffect) -> list[str]:
        return [d for d in run.device_ids if self._effect_of.get(d) == run_id]

    async def _send(self, run_id: str, run: RunningEffect, make) -> None:
        ids = self._active(run_id, run)
        await asyncio.gather(
            *(self._devices.execute(d, make(i, len(ids)), source=SOURCE) for i, d in enumerate(ids)),
            return_exceptions=True,
        )

    async def _run(self, run_id: str, run: RunningEffect) -> None:
        try:
            await self._send(run_id, run, lambda i, n: SetPower(on=True))
            step = 0
            while self._active(run_id, run):
                if run.effect == "rainbow":
                    def color(i: int, n: int, s: int = step) -> SetColor:
                        hue = (s / 40 + i / max(n, 1)) % 1.0
                        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
                        return SetColor(color=RGB(r=round(r * 255), g=round(g * 255), b=round(b * 255)))

                    await self._send(run_id, run, color)
                    await _sleep(1.5)
                elif run.effect == "breathe":
                    level = 8 + round(92 * (1 - math.cos(2 * math.pi * (step % 10) / 10)) / 2)
                    await self._send(run_id, run, lambda i, n, lv=level: SetBrightness(brightness=max(1, lv)))
                    await _sleep(1.0)
                else:  # candle
                    g = self._rng.randint(70, 110)
                    level = self._rng.randint(25, 60)
                    await self._send(run_id, run, lambda i, n, g=g: SetColor(color=RGB(r=255, g=g, b=12)))
                    await self._send(run_id, run, lambda i, n, lv=level: SetBrightness(brightness=lv))
                    await _sleep(self._rng.uniform(0.5, 1.3))
                step += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("effect crashed", extra={"ctx": {"effect": run.effect}})
        finally:
            for device_id in list(run.device_ids):
                if self._effect_of.get(device_id) == run_id:
                    self._effect_of.pop(device_id, None)
            self._runs.pop(run_id, None)

    async def _log(self, device_ids: list[str], text: str, source: str) -> None:
        if self._activity is None:
            return
        for device_id in device_ids:
            device = self._devices.get_device(device_id)
            await self._activity.add(
                ts=utcnow(), source=source, device_id=device_id, device_name=device.name,
                command=text, outcome="confirmed",
            )
        self._bus.publish(EventType.ACTIVITY_ADDED)

    def _changed(self) -> None:
        self._bus.publish(EventType.EFFECTS_CHANGED, {"running": [r.model_dump(mode="json") for r in self.running()]})

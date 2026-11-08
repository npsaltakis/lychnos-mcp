"""Vacation mode: switch lights on and off at random in the evening so the home looks lived in.

Every evening between the start (sunset or a fixed time) and the end time, each chosen light is
switched on for a random 30–90 minutes, then off for 10–45 minutes, and so on. At the end time
every light this mode switched on is switched off. Lights it did not switch on are left alone.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.models import Capability, CommandOutcome, SetPower, utcnow
from app.persistence.repositories import SettingsRepository
from app.services.device_service import DeviceService
from app.services.events import EventBus, EventType
from app.services.sun import sun_event

log = logging.getLogger(__name__)

SETTINGS_KEY = "vacation"
SOURCE = "vacation"
TICK_S = 60.0
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
ON_MIN = (30, 90)
OFF_MIN = (10, 45)


class VacationSettings(BaseModel):
    enabled: bool = False
    start: Literal["sunset", "time"] = "sunset"
    start_time: str = "19:30"
    end_time: str = "23:30"
    device_ids: list[str] = Field(default_factory=list, description="Empty = every registered light")

    @field_validator("start_time", "end_time")
    @classmethod
    def _time(cls, value: str) -> str:
        if not _TIME_RE.match(value):
            raise ValueError("time must be HH:MM (24h)")
        return value


class VacationStatus(BaseModel):
    settings: VacationSettings
    active: bool
    window_start: datetime | None
    window_end: datetime | None
    lights_on: list[str]


def _at(day: date, hhmm: str) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute).astimezone()


class VacationService:
    def __init__(
        self,
        settings_repo: SettingsRepository,
        devices: DeviceService,
        bus: EventBus,
        location_getter,
        *,
        tick_s: float = TICK_S,
        rng: random.Random | None = None,
    ) -> None:
        self._repo = settings_repo
        self._devices = devices
        self._bus = bus
        self._location = location_getter
        self._tick_s = tick_s
        self._rng = rng or random.Random()
        self._settings = VacationSettings()
        self._next: dict[str, datetime] = {}  # device id -> next toggle
        self._owned: set[str] = set()  # lights this mode switched on
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        raw = await self._repo.get(SETTINGS_KEY)
        if raw:
            self._settings = VacationSettings.model_validate(raw)
        self._task = asyncio.create_task(self._loop(), name="vacation")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------------ API

    def status(self, now: datetime | None = None) -> VacationStatus:
        now = now or utcnow()
        window = self.window(now)
        return VacationStatus(
            settings=self._settings,
            active=self._settings.enabled and window is not None and window[0] <= now < window[1],
            window_start=window[0] if window else None,
            window_end=window[1] if window else None,
            lights_on=sorted(self._owned),
        )

    async def update(self, settings: VacationSettings) -> VacationStatus:
        await self._repo.set(SETTINGS_KEY, settings.model_dump())
        self._settings = settings
        if not settings.enabled:
            await self._finish()
        self._next.clear()
        self._bus.publish(EventType.AUTOMATIONS_CHANGED)
        await self.tick(utcnow())
        return self.status()

    # ------------------------------------------------------------------ schedule

    def window(self, now: datetime) -> tuple[datetime, datetime] | None:
        """The current evening window, or the next one if none is running now."""
        today = now.astimezone().date()
        upcoming = None
        for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
            start = self._start_on(day)
            if start is None:
                continue
            end = _at(day, self._settings.end_time)
            if end <= start:
                end += timedelta(days=1)
            if start <= now < end:
                return start, end
            if start > now and upcoming is None:
                upcoming = (start, end)
        return upcoming

    def _start_on(self, day: date) -> datetime | None:
        if self._settings.start == "time":
            return _at(day, self._settings.start_time)
        loc = self._location()
        if loc is None:  # no location: fall back to the fixed start time
            return _at(day, self._settings.start_time)
        return sun_event(day, loc.latitude, loc.longitude, "sunset")

    def _lights(self) -> list[str]:
        lights = [d for d in self._devices.devices_in(None) if d.capabilities.has(Capability.POWER)]
        chosen = set(self._settings.device_ids)
        return [d.id for d in lights if not chosen or d.id in chosen]

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._tick_s)
            try:
                await self.tick(utcnow())
            except Exception:
                log.exception("vacation tick failed")

    async def tick(self, now: datetime) -> None:
        window = self.window(now) if self._settings.enabled else None
        if window is None or not (window[0] <= now < window[1]):
            await self._finish()
            return
        end = window[1]
        for device_id in self._lights():
            due = self._next.get(device_id)
            if due is None:  # first time in this window: start at a random moment soon
                self._next[device_id] = now + timedelta(minutes=self._rng.randint(0, 20))
                continue
            if now < due:
                continue
            turn_on = device_id not in self._owned
            if turn_on and end - now < timedelta(minutes=15):
                self._next[device_id] = end  # too close to the end to bother
                continue
            await self._switch(device_id, turn_on)
            lo, hi = ON_MIN if turn_on else OFF_MIN
            self._next[device_id] = now + timedelta(minutes=self._rng.randint(lo, hi))

    async def _switch(self, device_id: str, on: bool) -> None:
        try:
            result = await self._devices.execute(device_id, SetPower(on=on), source=SOURCE)
        except Exception as exc:
            log.warning("vacation switch failed", extra={"ctx": {"device_id": device_id, "error": str(exc)}})
            return
        if on and result.outcome is not CommandOutcome.FAILED:
            self._owned.add(device_id)
        elif not on:
            self._owned.discard(device_id)

    async def _finish(self) -> None:
        for device_id in list(self._owned):
            await self._switch(device_id, False)
        self._owned.clear()
        self._next.clear()

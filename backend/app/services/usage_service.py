"""Usage statistics: how many hours each light was on, per day.

Every confirmed on/off change (from Lychnos, Google Home, the vendor app or a wall switch) is
stored as a power event. A light that goes offline counts as off from that moment.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Coroutine

from pydantic import BaseModel

from app.domain.models import Availability, DeviceState, utcnow
from app.persistence.repositories import PowerEventRepository
from app.services.device_service import DeviceService
from app.services.state_store import StateStore

log = logging.getLogger(__name__)


class DeviceUsage(BaseModel):
    device_id: str
    name: str
    hours: list[float]  # one value per day, same order as UsageReport.days
    total_hours: float


class UsageReport(BaseModel):
    days: list[date]
    devices: list[DeviceUsage]


def _local_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day).astimezone()


def on_seconds(events: list[tuple[datetime, bool]], start: datetime, end: datetime) -> float:
    """Seconds inside [start, end) during which the light was on, given its sorted events."""
    total = 0.0
    on_since: datetime | None = None
    for ts, is_on in events:
        if ts >= end:
            break
        if is_on and on_since is None:
            on_since = max(ts, start)
        elif not is_on and on_since is not None:
            if ts > start:
                total += (ts - max(on_since, start)).total_seconds()
            on_since = None
    if on_since is not None:
        total += max(0.0, (end - max(on_since, start)).total_seconds())
    return total


class UsageService:
    def __init__(self, repo: PowerEventRepository, devices: DeviceService, store: StateStore) -> None:
        self._repo = repo
        self._devices = devices
        self._tasks: set[asyncio.Task[None]] = set()
        store.listeners.append(self._on_state_change)

    def _on_state_change(
        self, device_id: str, old: DeviceState, new: DeviceState, old_av: Availability, new_av: Availability
    ) -> None:
        now = utcnow()
        if new_av is Availability.OFFLINE and old_av is not Availability.OFFLINE:
            self._spawn(self._repo.add(device_id, now, False))
        elif new.power is not None and new.power != old.power:
            self._spawn(self._repo.add(device_id, now, new.power))

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("usage event write failed", exc_info=task.exception())

    async def flush(self) -> None:
        """Wait for pending writes (used by tests and before reports)."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def report(self, days: int = 7, now: datetime | None = None) -> UsageReport:
        await self.flush()
        now = now or utcnow()
        today = now.astimezone().date()
        day_list = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
        since = _local_midnight(day_list[0])
        events = await self._repo.between(since, now)
        out: list[DeviceUsage] = []
        for view in self._devices.list_devices(registered=True):
            dev_events = events.get(view.device.id, [])
            hours = []
            for day in day_list:
                start = _local_midnight(day)
                end = min(_local_midnight(day + timedelta(days=1)), now)
                hours.append(round(on_seconds(dev_events, start, end) / 3600, 2) if end > start else 0.0)
            out.append(DeviceUsage(device_id=view.device.id, name=view.device.name, hours=hours, total_hours=round(sum(hours), 2)))
        out.sort(key=lambda d: d.total_hours, reverse=True)
        return UsageReport(days=day_list, devices=out)

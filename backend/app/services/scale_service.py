"""Bathroom scale (Eufy Smart Scale C20 / T9130) read over Bluetooth, locally, without the Eufy cloud.

The scale broadcasts BLE advertisements while someone stands on it; nothing has to be paired.
Manufacturer data (after the 2-byte company id, which is really the first byte of the MAC):

    0-4   rest of the scale's MAC
    5     0x00
    6-9   scale clock, unix seconds, little-endian
    10    flags (0x01 measuring ... 0x25 done)
    11    0x80 = weight is final (locked); 0x40 = stepped off
    12-13 weight in 1/100 kg, little-endian

Only the final (locked) weight of each weighing is stored. When no scale is chosen yet, the first
Eufy scale ("eufy T91xx") that is stood on is adopted and remembered.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Coroutine, Literal

from pydantic import BaseModel, Field

from app.domain.models import Weighing, utcnow
from app.persistence.repositories import SettingsRepository, WeighingRepository
from app.services.events import EventBus, EventType

log = logging.getLogger(__name__)

SETTINGS_KEY = "scale"
NAME_PREFIX = "eufy t91"
RETRY_S = 60.0
SAME_WEIGHING_S = 120.0  # a second "final" packet within this window is the same weighing

AdvCallback = Callable[[str, str, dict[int, bytes]], None]
ScanFn = Callable[[AdvCallback], Awaitable[None]]


@dataclass(frozen=True)
class Reading:
    kg: float
    final: bool
    stepped_off: bool


def parse_advertisement(data: bytes) -> Reading | None:
    """Decode one manufacturer-data payload of the scale (see the module docstring)."""
    if len(data) < 14 or data[5] != 0:
        return None
    kg = int.from_bytes(data[12:14], "little") / 100
    if kg > 250:
        return None
    return Reading(kg=kg, final=bool(data[11] & 0x80) and kg > 0, stepped_off=bool(data[11] & 0x40))


class ScaleSettings(BaseModel):
    enabled: bool = False
    address: str = Field("", max_length=40, description="Bluetooth address of the chosen scale; empty = adopt the first one")
    name: str = Field("", max_length=60)


class ScaleStatus(BaseModel):
    settings: ScaleSettings
    bluetooth: Literal["off", "listening", "unavailable"]
    error: str | None = None
    last_seen: datetime | None = None
    latest: Weighing | None = None


async def bleak_scan(callback: AdvCallback) -> None:
    """Listen to BLE advertisements forever (raises if there is no Bluetooth adapter)."""
    from bleak import BleakScanner

    def on_adv(device: Any, adv: Any) -> None:
        callback(device.address, adv.local_name or device.name or "", dict(adv.manufacturer_data))

    async with BleakScanner(on_adv):
        await asyncio.Event().wait()


class ScaleService:
    def __init__(self, settings_repo: SettingsRepository, repo: WeighingRepository, bus: EventBus, scan: ScanFn | None = None) -> None:
        self._settings_repo = settings_repo
        self._repo = repo
        self._bus = bus
        self._scan = scan or bleak_scan
        self._settings = ScaleSettings()
        self._task: asyncio.Task[None] | None = None
        self._bluetooth: Literal["off", "listening", "unavailable"] = "off"
        self._error: str | None = None
        self._last_seen: datetime | None = None
        self._latest: Weighing | None = None
        self._last_final_at: datetime | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        raw = await self._settings_repo.get(SETTINGS_KEY)
        if isinstance(raw, dict):
            self._settings = ScaleSettings.model_validate(raw)
        self._latest = await self._repo.latest()
        self._apply()

    async def stop(self) -> None:
        await self._stop_scan()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def status(self) -> ScaleStatus:
        return ScaleStatus(
            settings=self._settings, bluetooth=self._bluetooth, error=self._error,
            last_seen=self._last_seen, latest=self._latest,
        )

    async def update(self, settings: ScaleSettings) -> ScaleStatus:
        if settings.address != self._settings.address:
            settings = settings.model_copy(update={"name": "" if not settings.address else settings.name})
        self._settings = settings
        await self._settings_repo.set(SETTINGS_KEY, settings.model_dump())
        await self._stop_scan()
        self._apply()
        return self.status()

    async def weighings(self, days: int) -> list[Weighing]:
        return await self._repo.since(utcnow() - timedelta(days=days))

    async def delete(self, weighing_id: int) -> bool:
        deleted = await self._repo.delete(weighing_id)
        if deleted:
            self._latest = await self._repo.latest()
            self._bus.publish(EventType.WEIGHT_RECORDED, {"deleted": weighing_id})
        return deleted

    # ------------------------------------------------------------------ scanning

    def _apply(self) -> None:
        if self._settings.enabled and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="scale")
        elif not self._settings.enabled:
            self._bluetooth = "off"

    async def _stop_scan(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._bluetooth = "off"

    async def _loop(self) -> None:
        while True:
            try:
                self._bluetooth, self._error = "listening", None
                await self._scan(self.on_advertisement)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._bluetooth, self._error = "unavailable", str(exc) or exc.__class__.__name__
                log.warning("scale bluetooth unavailable", extra={"ctx": {"error": self._error}})
            await asyncio.sleep(RETRY_S)

    def on_advertisement(self, address: str, name: str, manufacturer: dict[int, bytes], now: datetime | None = None) -> None:
        wanted = self._settings.address
        if wanted:
            if address.upper() != wanted.upper():
                return
        elif not name.lower().startswith(NAME_PREFIX):
            return
        reading = next((r for r in map(parse_advertisement, manufacturer.values()) if r is not None), None)
        if reading is None:
            return
        now = now or utcnow()
        self._last_seen = now
        if not wanted:
            self._settings = self._settings.model_copy(update={"address": address.upper(), "name": name})
            self._spawn(self._settings_repo.set(SETTINGS_KEY, self._settings.model_dump()))
            log.info("scale adopted", extra={"ctx": {"name": name}})
        if not reading.final:
            return
        if self._last_final_at is not None and (now - self._last_final_at).total_seconds() < SAME_WEIGHING_S:
            return
        self._last_final_at = now
        self._spawn(self._record(now, reading.kg))

    async def _record(self, ts: datetime, kg: float) -> None:
        previous = self._latest
        weighing = await self._repo.add(ts, kg)
        self._latest = weighing
        log.info("weight recorded", extra={"ctx": {"kg": kg}})
        self._bus.publish(
            EventType.WEIGHT_RECORDED,
            {
                "weighing": weighing.model_dump(mode="json"),
                "previous": previous.model_dump(mode="json") if previous else None,
            },
        )

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("scale write failed", exc_info=task.exception())

    async def flush(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

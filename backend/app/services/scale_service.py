"""Bathroom scales read over Bluetooth, locally, without any cloud.

Scales broadcast BLE advertisements while someone stands on them; nothing has to be paired. They
are found with the same "Search" as the lights (stand on the scale while it searches) and then
added. Only the final (locked) weight of each weighing is stored.

Supported formats:

* Eufy Smart Scale C20 (T9130), tested. Manufacturer data, after the 2-byte company id
  (really the first byte of the MAC):
      0-4 rest of the MAC · 5 0x00 · 6-9 scale clock (unix, LE) · 10 flags
      11 0x80 = final, 0x40 = stepped off · 12-13 weight in 1/100 kg (LE)
* Xiaomi Mi Body Composition Scale 2 (service data 0x181B) and Mi Smart Scale
  (service data 0x181D), from the published format (as used by openScale); not tested here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Coroutine, Literal

from pydantic import BaseModel, Field

from app.core.errors import CommandFailedError, NotFoundError
from app.domain.models import Weighing, utcnow
from app.persistence.repositories import SettingsRepository, WeighingRepository
from app.services.events import EventBus, EventType

log = logging.getLogger(__name__)

SETTINGS_KEY = "scale"
RETRY_S = 60.0
SAME_WEIGHING_S = 120.0  # a second "final" packet within this window is the same weighing
XIAOMI_V2_UUID = "0000181b-0000-1000-8000-00805f9b34fb"
XIAOMI_V1_UUID = "0000181d-0000-1000-8000-00805f9b34fb"

# address, name, manufacturer data, service data
AdvCallback = Callable[[str, str, dict[int, bytes], dict[str, bytes]], None]
ScanFn = Callable[[AdvCallback], Awaitable[None]]


@dataclass(frozen=True)
class Reading:
    kg: float
    final: bool
    stepped_off: bool = False


# --------------------------------------------------------------------------- decoders


def parse_eufy(data: bytes) -> Reading | None:
    if len(data) < 14 or data[5] != 0:
        return None
    kg = int.from_bytes(data[12:14], "little") / 100
    if kg > 250:
        return None
    return Reading(kg=kg, final=bool(data[11] & 0x80) and kg > 0, stepped_off=bool(data[11] & 0x40))


# Kept for the C20 tests and callers that only have manufacturer data.
parse_advertisement = parse_eufy


def _xiaomi_kg(raw: int, lbs: bool, catty: bool) -> float:
    if lbs:
        return round(raw / 100 * 0.45359237, 2)
    if catty:
        return round(raw / 100 * 0.5, 2)
    return round(raw / 200, 2)


def parse_xiaomi_v2(data: bytes) -> Reading | None:
    """Mi Body Composition Scale 2: ctrl0 ctrl1 · date (7) · impedance (2) · weight (2)."""
    if len(data) < 13:
        return None
    ctrl0, ctrl1 = data[0], data[1]
    removed = bool(ctrl1 & 0x80)
    kg = _xiaomi_kg(int.from_bytes(data[11:13], "little"), bool(ctrl0 & 0x01), bool(ctrl1 & 0x40))
    return Reading(kg=kg, final=bool(ctrl1 & 0x20) and not removed and kg > 0, stepped_off=removed)


def parse_xiaomi_v1(data: bytes) -> Reading | None:
    """Mi Smart Scale (1st gen): ctrl · weight (2) · date..."""
    if len(data) < 3:
        return None
    ctrl = data[0]
    removed = bool(ctrl & 0x80)
    kg = _xiaomi_kg(int.from_bytes(data[1:3], "little"), bool(ctrl & 0x01), bool(ctrl & 0x10))
    return Reading(kg=kg, final=bool(ctrl & 0x20) and not removed and kg > 0, stepped_off=removed)


@dataclass(frozen=True)
class ScaleModel:
    key: str
    label: str
    decode: Callable[[str, dict[int, bytes], dict[str, bytes]], Reading | None]


def _eufy(name: str, mfr: dict[int, bytes], svc: dict[str, bytes]) -> Reading | None:
    if not name.lower().startswith("eufy t913"):
        return None
    return next((r for r in map(parse_eufy, mfr.values()) if r is not None), None)


def _xiaomi2(name: str, mfr: dict[int, bytes], svc: dict[str, bytes]) -> Reading | None:
    data = svc.get(XIAOMI_V2_UUID)
    return parse_xiaomi_v2(data) if data else None


def _xiaomi1(name: str, mfr: dict[int, bytes], svc: dict[str, bytes]) -> Reading | None:
    data = svc.get(XIAOMI_V1_UUID)
    return parse_xiaomi_v1(data) if data else None


MODELS: dict[str, ScaleModel] = {
    m.key: m
    for m in (
        ScaleModel("eufy_c20", "Eufy Smart Scale C20", _eufy),
        ScaleModel("xiaomi_body2", "Xiaomi Mi Body Composition Scale 2", _xiaomi2),
        ScaleModel("xiaomi_scale", "Xiaomi Mi Smart Scale", _xiaomi1),
    )
}


def identify(name: str, mfr: dict[int, bytes], svc: dict[str, bytes]) -> tuple[ScaleModel, Reading] | None:
    for model in MODELS.values():
        reading = model.decode(name, mfr, svc)
        if reading is not None:
            return model, reading
    return None


# --------------------------------------------------------------------------- models


class ScaleEntry(BaseModel):
    address: str = Field(max_length=40)
    name: str = Field(max_length=60)
    model: str = Field(max_length=40)


class ScaleSettings(BaseModel):
    scales: list[ScaleEntry] = Field(default_factory=list)


class ScaleView(ScaleEntry):
    model_label: str
    last_seen: datetime | None = None
    last_kg: float | None = None


class ScaleCandidate(BaseModel):
    address: str
    name: str
    model: str
    model_label: str
    kg: float | None = None


class ScaleStatus(BaseModel):
    scales: list[ScaleView]
    bluetooth: Literal["off", "listening", "unavailable"]
    error: str | None = None
    latest: Weighing | None = None


class AddScale(BaseModel):
    address: str = Field(max_length=40)
    name: str | None = Field(None, max_length=60)


class RenameScale(BaseModel):
    name: str = Field(min_length=1, max_length=60)


async def bleak_scan(callback: AdvCallback) -> None:
    """Listen to BLE advertisements forever (raises if there is no Bluetooth adapter)."""
    from bleak import BleakScanner

    def on_adv(device: Any, adv: Any) -> None:
        callback(
            device.address, adv.local_name or device.name or "",
            dict(adv.manufacturer_data), {k.lower(): v for k, v in adv.service_data.items()},
        )

    async with BleakScanner(on_adv):
        await asyncio.Event().wait()


@dataclass
class _Seen:
    name: str
    model: ScaleModel
    at: datetime
    kg: float | None


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
        self._seen: dict[str, _Seen] = {}
        self._latest: Weighing | None = None
        self._last_final_at: dict[str, datetime] = {}
        self._discovering = 0
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        raw = await self._settings_repo.get(SETTINGS_KEY)
        if isinstance(raw, dict):
            if raw.get("address"):  # first version: one scale, {"enabled", "address", "name"}
                raw = {"scales": [{"address": raw["address"], "name": raw.get("name") or "Ζυγαριά", "model": "eufy_c20"}]}
            self._settings = ScaleSettings.model_validate({"scales": raw.get("scales", [])})
        self._latest = await self._repo.latest()
        self._apply()

    async def stop(self) -> None:
        await self._stop_scan()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def status(self) -> ScaleStatus:
        views = []
        for s in self._settings.scales:
            seen = self._seen.get(s.address)
            model = MODELS.get(s.model)
            views.append(ScaleView(
                **s.model_dump(), model_label=model.label if model else s.model,
                last_seen=seen.at if seen else None, last_kg=seen.kg if seen else None,
            ))
        return ScaleStatus(scales=views, bluetooth=self._bluetooth, error=self._error, latest=self._latest)

    # ------------------------------------------------------------------ managing scales

    def _added(self, address: str) -> ScaleEntry | None:
        return next((s for s in self._settings.scales if s.address == address.upper()), None)

    async def _save(self) -> None:
        await self._settings_repo.set(SETTINGS_KEY, self._settings.model_dump())
        self._apply()
        self._bus.publish(EventType.WEIGHT_RECORDED, {"scales_changed": True})

    async def discover(self, timeout_s: float) -> list[ScaleCandidate]:
        """Listen for `timeout_s` and return scales that sent a reading and are not added yet."""
        started = utcnow()
        self._discovering += 1
        self._apply()
        try:
            await asyncio.sleep(timeout_s)
        finally:
            self._discovering -= 1
            if not self._settings.scales and not self._discovering:
                await self._stop_scan()
        if self._bluetooth == "unavailable":
            raise CommandFailedError(f"Bluetooth is not available: {self._error}", code="bluetooth_unavailable")
        return [
            ScaleCandidate(address=a, name=s.name or s.model.label, model=s.model.key, model_label=s.model.label, kg=s.kg)
            for a, s in self._seen.items()
            if s.at >= started - timedelta(seconds=60) and not self._added(a)
        ]

    async def add(self, body: AddScale) -> ScaleStatus:
        address = body.address.upper()
        if not self._added(address):
            seen = self._seen.get(address)
            if seen is None:
                raise NotFoundError("Scale not found; search again while standing on it", details={"address": address})
            self._settings.scales.append(ScaleEntry(address=address, name=body.name or seen.name or seen.model.label, model=seen.model.key))
            await self._save()
        return self.status()

    async def rename(self, address: str, name: str) -> ScaleStatus:
        entry = self._added(address)
        if entry is None:
            raise NotFoundError("Scale not found", details={"address": address})
        entry.name = name.strip()
        await self._save()
        return self.status()

    async def remove(self, address: str) -> ScaleStatus:
        if self._added(address) is None:
            raise NotFoundError("Scale not found", details={"address": address})
        self._settings.scales = [s for s in self._settings.scales if s.address != address.upper()]
        await self._save()
        return self.status()

    # ------------------------------------------------------------------ weighings

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
        wanted = bool(self._settings.scales) or self._discovering > 0
        if wanted and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="scale")
        elif not wanted and self._task is not None:
            self._spawn(self._stop_scan())

    async def _stop_scan(self) -> None:
        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
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

    def on_advertisement(
        self, address: str, name: str, manufacturer: dict[int, bytes], service_data: dict[str, bytes] | None = None,
        now: datetime | None = None,
    ) -> None:
        found = identify(name, manufacturer, service_data or {})
        if found is None:
            return
        model, reading = found
        address = address.upper()
        now = now or utcnow()
        previous = self._seen.get(address)
        self._seen[address] = _Seen(
            name=name or (previous.name if previous else ""), model=model, at=now,
            kg=reading.kg if reading.kg > 0 else (previous.kg if previous else None),
        )
        if not reading.final or self._added(address) is None:
            return
        last = self._last_final_at.get(address)
        if last is not None and (now - last).total_seconds() < SAME_WEIGHING_S:
            return
        self._last_final_at[address] = now
        self._spawn(self._record(now, reading.kg, address))

    async def _record(self, ts: datetime, kg: float, scale: str) -> None:
        previous = self._latest
        weighing = await self._repo.add(ts, kg, scale)
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
            log.error("scale task failed", exc_info=task.exception())

    async def flush(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

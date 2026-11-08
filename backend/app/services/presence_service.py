"""Presence: is anyone home? Decided by whether the phones are on the home Wi-Fi.

Every minute Lychnos pokes each address of the local network with one tiny UDP packet (so the
computer asks "who has this address?" on the LAN) and then reads the computer's neighbour table
(Windows: Get-NetNeighbor, Linux: ip neigh). A phone counts as home while its MAC address answers.
Phones put Wi-Fi to sleep, so someone counts as away only after ``away_after_min`` minutes
without an answer.

Actions (both optional):
- the last person leaves → switch every light off
- the first person arrives → switch the lights on or run a scene (optionally only when it is dark)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import sys
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field, field_validator

from app.core.net import local_subnet_ips
from app.domain.models import SetPower, utcnow
from app.persistence.repositories import SettingsRepository
from app.services.device_service import DeviceService
from app.services.events import EventBus, EventType
from app.services.scene_service import SceneService
from app.services.sun import sun_event

log = logging.getLogger(__name__)

SETTINGS_KEY = "presence"
SOURCE = "presence"
TICK_S = 60.0
SWEEP_EVERY = timedelta(minutes=10)
_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
PRESENT_STATES = {"reachable"}  # "probe"/"delay" only mean the PC is still asking


def normalize_mac(value: str) -> str:
    hexes = re.sub(r"[^0-9a-fA-F]", "", value).lower()
    if len(hexes) != 12:
        return value.strip().lower()
    return ":".join(hexes[i : i + 2] for i in range(0, 12, 2))


class Person(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    mac: str

    @field_validator("mac")
    @classmethod
    def _mac(cls, value: str) -> str:
        mac = normalize_mac(value)
        if not _MAC_RE.match(mac):
            raise ValueError("MAC address must look like aa:bb:cc:dd:ee:ff")
        return mac


class PresenceSettings(BaseModel):
    enabled: bool = False
    people: list[Person] = Field(default_factory=list, max_length=10)
    away_after_min: int = Field(15, ge=3, le=120)
    on_leave: Literal["nothing", "all_off"] = "all_off"
    on_arrive: Literal["nothing", "all_on", "scene"] = "nothing"
    arrive_scene_id: str | None = None
    arrive_only_dark: bool = True


class PersonStatus(BaseModel):
    name: str
    mac: str
    home: bool
    last_seen: datetime | None
    ip: str | None


class PresenceStatus(BaseModel):
    settings: PresenceSettings
    anyone_home: bool | None
    people: list[PersonStatus]


class Neighbor(BaseModel):
    ip: str
    mac: str
    state: str


class NeighborScanner(Protocol):
    async def ping(self, ip: str) -> bool: ...
    async def probe(self, ips: list[str]) -> None: ...
    async def neighbors(self) -> list[Neighbor]: ...
    def subnet_ips(self) -> list[str]: ...


class SystemScanner:
    """Uses ping and the operating system's ARP / neighbour table."""

    async def ping(self, ip: str) -> bool:
        """Two pings, 1.5 s each: a sleeping phone may take a moment to wake its Wi-Fi."""
        if sys.platform == "win32":
            cmd = ["ping", "-n", "2", "-w", "1500", ip]
        else:
            cmd = ["ping", "-c", "2", "-W", "2", ip]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (OSError, TimeoutError):
            return False
        # Windows ping exits 0 on "Destination host unreachable" too: require a TTL in a reply line.
        return proc.returncode == 0 and b"TTL=" in out.upper()

    async def probe(self, ips: list[str]) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            for ip in ips:
                try:
                    sock.sendto(b"", (ip, 9))  # "discard" port; only the ARP lookup matters
                except OSError:
                    pass
        finally:
            sock.close()
        await asyncio.sleep(2.5)

    async def neighbors(self) -> list[Neighbor]:
        if sys.platform == "win32":
            cmd = [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-NetNeighbor -AddressFamily IPv4 | Select-Object IPAddress,LinkLayerAddress,"
                "@{n='State';e={\"$($_.State)\"}} | ConvertTo-Json -Compress",
            ]
        else:
            cmd = ["ip", "-j", "-4", "neigh"]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        data = json.loads(out.decode(errors="replace") or "[]")
        if isinstance(data, dict):
            data = [data]
        result = []
        for row in data:
            if sys.platform == "win32":
                ip, mac, state = row.get("IPAddress"), row.get("LinkLayerAddress"), row.get("State", "")
            else:
                ip, mac = row.get("dst"), row.get("lladdr")
                state = (row.get("state") or [""])[0]
            if ip and mac and normalize_mac(mac) != "00:00:00:00:00:00":
                result.append(Neighbor(ip=ip, mac=normalize_mac(mac), state=str(state).lower()))
        return result

    def subnet_ips(self) -> list[str]:
        return local_subnet_ips()


class PresenceService:
    def __init__(
        self,
        settings_repo: SettingsRepository,
        devices: DeviceService,
        scenes: SceneService,
        bus: EventBus,
        location_getter,
        scanner: NeighborScanner | None = None,
        *,
        tick_s: float = TICK_S,
    ) -> None:
        self._repo = settings_repo
        self._devices = devices
        self._scenes = scenes
        self._bus = bus
        self._location = location_getter
        self._scanner = scanner or SystemScanner()
        self._tick_s = tick_s
        self._settings = PresenceSettings()
        self._last_seen: dict[str, datetime] = {}
        self._ip: dict[str, str] = {}
        self._anyone_home: bool | None = None
        self._last_sweep: datetime | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        raw = await self._repo.get(SETTINGS_KEY)
        if raw:
            self._settings = PresenceSettings.model_validate(raw)
        self._task = asyncio.create_task(self._loop(), name="presence")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------------ API

    def status(self, now: datetime | None = None) -> PresenceStatus:
        now = now or utcnow()
        people = [
            PersonStatus(
                name=p.name, mac=p.mac, home=self._is_home(p.mac, now),
                last_seen=self._last_seen.get(p.mac), ip=self._ip.get(p.mac),
            )
            for p in self._settings.people
        ]
        return PresenceStatus(settings=self._settings, anyone_home=self._anyone_home, people=people)

    async def update(self, settings: PresenceSettings) -> PresenceStatus:
        if settings.on_arrive == "scene":
            if not settings.arrive_scene_id:
                from app.core.errors import InvalidCommandError

                raise InvalidCommandError("Choose the scene to run on arrival")
            await self._scenes.get(settings.arrive_scene_id)
        await self._repo.set(SETTINGS_KEY, settings.model_dump())
        self._settings = settings
        self._anyone_home = None  # re-learn without firing actions
        self._last_sweep = None
        self._bus.publish(EventType.AUTOMATIONS_CHANGED)
        return self.status()

    async def network(self) -> list[Neighbor]:
        """Devices currently on the LAN, to help pick the phone's MAC address."""
        await self._scanner.probe(self._scanner.subnet_ips())
        rows = await self._scanner.neighbors()
        return sorted(
            (n for n in rows if n.state in PRESENT_STATES | {"stale"}),
            key=lambda n: tuple(int(x) for x in n.ip.split(".")),
        )

    # ------------------------------------------------------------------ loop

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._tick_s)
            if not self._settings.enabled or not self._settings.people:
                continue
            try:
                await self.scan(utcnow())
            except Exception:
                log.exception("presence scan failed")

    async def scan(self, now: datetime) -> None:
        macs = {p.mac for p in self._settings.people}
        if self._last_sweep is None or now - self._last_sweep >= SWEEP_EVERY or any(m not in self._ip for m in macs):
            targets = self._scanner.subnet_ips()
            self._last_sweep = now
        else:
            targets = [self._ip[m] for m in macs if m in self._ip]
        await self._scanner.probe(targets)
        seen: set[str] = set()
        for n in await self._scanner.neighbors():
            if n.mac in macs:
                self._ip[n.mac] = n.ip
                if n.state in PRESENT_STATES:
                    seen.add(n.mac)
        for mac in macs - seen:  # not answered yet: a ping to its address also counts as home
            ip = self._ip.get(mac)
            if ip and await self._scanner.ping(ip):
                seen.add(mac)
        for mac in seen:
            self._last_seen[mac] = now
        await self.evaluate(now)

    def _is_home(self, mac: str, now: datetime) -> bool:
        seen = self._last_seen.get(mac)
        return seen is not None and now - seen < timedelta(minutes=self._settings.away_after_min)

    async def evaluate(self, now: datetime) -> None:
        home = any(self._is_home(p.mac, now) for p in self._settings.people)
        before = self._anyone_home
        self._anyone_home = home
        if before is None or before == home:
            return  # first reading after start/change: learn only, do nothing
        log.info("presence changed", extra={"ctx": {"anyone_home": home}})
        self._bus.publish(EventType.AUTOMATIONS_CHANGED)
        s = self._settings
        if not home and s.on_leave == "all_off":
            await self._devices.execute_on_group(SetPower(on=False), room_id=None, source=SOURCE)
        elif home and s.on_arrive != "nothing" and (not s.arrive_only_dark or self._is_dark(now)):
            if s.on_arrive == "scene" and s.arrive_scene_id:
                await self._scenes.activate(s.arrive_scene_id, source=SOURCE)
            elif s.on_arrive == "all_on":
                await self._devices.execute_on_group(SetPower(on=True), room_id=None, source=SOURCE)

    def _is_dark(self, now: datetime) -> bool:
        loc = self._location()
        local = now.astimezone()
        if loc is None:
            return local.hour >= 18 or local.hour < 7
        rise = sun_event(local.date(), loc.latitude, loc.longitude, "sunrise")
        sset = sun_event(local.date(), loc.latitude, loc.longitude, "sunset")
        if rise is None or sset is None:
            return False
        return now < rise or now >= sset - timedelta(minutes=30)

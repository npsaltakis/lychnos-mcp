"""Shelly LAN provider: on/off for Shelly relays and plugs over local HTTP, no cloud.

- Gen1 (Shelly 1, Plug S, …):        GET /relay/0, GET /relay/0?turn=on|off
- Gen2+ (Plus / Pro / Gen3, …):      GET /rpc/Switch.GetStatus?id=0, /rpc/Switch.Set?id=0&on=true
Discovery asks every address of the home network for ``/shelly`` (each Shelly answers it).
Shellys with a password (restricted login) are not supported yet.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import DeviceOfflineError, DeviceTimeoutError, InvalidCommandError, ProviderError
from app.core.net import local_subnet_ips
from app.domain.models import (
    Capabilities,
    Capability,
    Command,
    DeviceRecord,
    DeviceState,
    DiscoveredDevice,
    SetPower,
)
from app.providers.base import DeviceProvider

log = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 0.8
REQUEST_TIMEOUT_S = 3.0
CONCURRENCY = 128
SWITCH_CAPS = Capabilities(supported=frozenset({Capability.POWER}), supports_state_query=True)


def parse_identity(ip: str, info: dict[str, Any]) -> dict[str, Any] | None:
    """Gen and model from a /shelly answer; None if it is not a Shelly."""
    mac = str(info.get("mac", "")).lower().replace(":", "")
    if not mac:
        return None
    if info.get("gen", 1) >= 2:
        return {"gen": int(info["gen"]), "mac": mac, "model": info.get("model") or info.get("app"), "name": info.get("name"), "ip": ip}
    if "type" in info:
        return {"gen": 1, "mac": mac, "model": info["type"], "name": None, "ip": ip}
    return None


class ShellyLanProvider(DeviceProvider):
    key = "shelly_lan"
    manufacturer = "Shelly"

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = http

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _get(self, ip: str, path: str, timeout: float = REQUEST_TIMEOUT_S) -> dict[str, Any]:
        try:
            response = await self._client().get(f"http://{ip}{path}", timeout=timeout)
        except httpx.TimeoutException:
            raise DeviceTimeoutError(f"Shelly at {ip} did not answer") from None
        except httpx.HTTPError as exc:
            raise DeviceOfflineError(f"Cannot reach Shelly at {ip}") from exc
        if response.status_code == 401:
            raise ProviderError(f"Shelly at {ip} is password-protected; turn off restricted login")
        if response.status_code >= 400:
            raise ProviderError(f"Shelly at {ip} answered HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise ProviderError(f"Shelly at {ip} sent an unexpected answer") from None
        return data if isinstance(data, dict) else {}

    async def discover(self, timeout_s: float) -> list[DiscoveredDevice]:
        sem = asyncio.Semaphore(CONCURRENCY)

        async def probe(ip: str) -> DiscoveredDevice | None:
            async with sem:
                try:
                    ident = parse_identity(ip, await self._get(ip, "/shelly", PROBE_TIMEOUT_S))
                except Exception:
                    return None
            if ident is None:
                return None
            try:  # only devices with a switch / relay output
                if ident["gen"] >= 2:
                    await self._get(ip, "/rpc/Switch.GetStatus?id=0")
                else:
                    await self._get(ip, "/relay/0")
            except Exception:
                return None
            return DiscoveredDevice(
                provider=self.key, native_id=ident["mac"],
                name=ident["name"] or f"Shelly {ident['mac'][-4:]}", manufacturer=self.manufacturer,
                model=ident["model"], address=ip, capabilities=SWITCH_CAPS, metadata={"gen": ident["gen"]},
            )

        results = await asyncio.wait_for(
            asyncio.gather(*(probe(ip) for ip in local_subnet_ips())), timeout=max(timeout_s, 8.0)
        )
        return [r for r in results if r is not None]

    @staticmethod
    def _gen(device: DeviceRecord) -> int:
        return int(device.metadata.get("gen", 1))

    def _ip(self, device: DeviceRecord) -> str:
        if not device.address:
            raise DeviceOfflineError(f"{device.name} has no known address; run a scan")
        return device.address

    async def get_state(self, device: DeviceRecord) -> DeviceState:
        ip = self._ip(device)
        if self._gen(device) >= 2:
            data = await self._get(ip, "/rpc/Switch.GetStatus?id=0")
            return DeviceState(power=bool(data.get("output")))
        data = await self._get(ip, "/relay/0")
        return DeviceState(power=bool(data.get("ison")))

    async def execute(self, device: DeviceRecord, command: Command) -> DeviceState | None:
        if not isinstance(command, SetPower):
            raise InvalidCommandError(f"{device.name} can only be switched on or off")
        ip = self._ip(device)
        if self._gen(device) >= 2:
            await self._get(ip, f"/rpc/Switch.Set?id=0&on={'true' if command.on else 'false'}")
        else:
            await self._get(ip, f"/relay/0?turn={'on' if command.on else 'off'}")
        return await self.get_state(device)

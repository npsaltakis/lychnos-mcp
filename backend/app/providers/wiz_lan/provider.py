"""WiZ LAN provider: JSON over UDP port 38899, no cloud, no keys.

WiZ lights answer ``getPilot`` (state) and ``setPilot`` (change) on the local network. LAN
control is on by default (WiZ app → Settings → Security → "Allow local communication").
Commands are confirmed by reading ``getPilot`` back.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.core.config import Settings
from app.core.errors import DeviceOfflineError, DeviceTimeoutError, ProviderError
from app.core.net import local_broadcast
from app.domain.models import (
    RGB,
    Capabilities,
    Capability,
    ColorTempRange,
    Command,
    DeviceRecord,
    DeviceState,
    DiscoveredDevice,
    LightMode,
    SetBrightness,
    SetColor,
    SetColorTemperature,
    SetPower,
)
from app.providers.base import DeviceProvider

log = logging.getLogger(__name__)

PORT = 38899
REQUEST_TIMEOUT_S = 1.5
RETRIES = 2


def capabilities_for(module: str) -> Capabilities:
    """Pick capabilities from the module name (e.g. ESP01_SHRGB1C_31, ESP56_SHTW3_01, ESP10_SOCKET_06)."""
    m = module.upper()
    if "SOCKET" in m:
        return Capabilities(supported=frozenset({Capability.POWER}), supports_state_query=True)
    base = {Capability.POWER, Capability.BRIGHTNESS}
    if "RGB" in m:
        return Capabilities(
            supported=frozenset(base | {Capability.COLOR_RGB, Capability.COLOR_TEMP}),
            brightness_min=10, color_temp_range=ColorTempRange(min_k=2200, max_k=6500), supports_state_query=True,
        )
    if "TW" in m:
        return Capabilities(
            supported=frozenset(base | {Capability.COLOR_TEMP}),
            brightness_min=10, color_temp_range=ColorTempRange(min_k=2700, max_k=6500), supports_state_query=True,
        )
    return Capabilities(supported=frozenset(base), brightness_min=10, supports_state_query=True)


def parse_pilot(result: dict[str, Any]) -> DeviceState:
    color = None
    mode = None
    if all(k in result for k in ("r", "g", "b")):
        color = RGB(r=int(result["r"]), g=int(result["g"]), b=int(result["b"]))
        mode = LightMode.COLOR
    temp = result.get("temp")
    if temp:
        mode = LightMode.WHITE
    dimming = result.get("dimming")
    return DeviceState(
        power=bool(result["state"]) if "state" in result else None,
        brightness=max(1, min(100, int(dimming))) if dimming is not None else None,
        color=color,
        color_temp_k=int(temp) if temp else None,
        mode=mode,
    )


def pilot_params(command: Command) -> dict[str, Any]:
    match command:
        case SetPower(on=on):
            return {"state": on}
        case SetBrightness(brightness=b):
            return {"state": True, "dimming": max(10, b)}
        case SetColor(color=c):
            return {"state": True, "r": c.r, "g": c.g, "b": c.b}
        case SetColorTemperature(kelvin=k):
            return {"state": True, "temp": k}
    raise ProviderError(f"WiZ does not support {command.type}")


class _Collector(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.replies: dict[str, dict[str, Any]] = {}
        self.waiters: list[asyncio.Future[dict[str, Any]]] = []

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            message = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(message, dict):
            self.replies[addr[0]] = message
            for waiter in self.waiters:
                if not waiter.done():
                    waiter.set_result(message)


class WizLanProvider(DeviceProvider):
    key = "wiz_lan"
    manufacturer = "WiZ"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def _request(self, ip: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        transport, proto = await loop.create_datagram_endpoint(_Collector, local_addr=("0.0.0.0", 0))
        try:
            payload = json.dumps({"method": method, "params": params or {}}).encode()
            for _attempt in range(RETRIES + 1):
                waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
                proto.waiters = [waiter]
                transport.sendto(payload, (ip, PORT))
                try:
                    message = await asyncio.wait_for(waiter, REQUEST_TIMEOUT_S)
                except TimeoutError:
                    continue
                if "error" in message:
                    raise ProviderError(f"WiZ at {ip} rejected {method}: {message['error']}")
                return message.get("result") or {}
            raise DeviceTimeoutError(f"WiZ light at {ip} did not answer")
        except OSError as exc:
            raise DeviceOfflineError(f"Cannot reach WiZ light at {ip}") from exc
        finally:
            transport.close()

    async def discover(self, timeout_s: float) -> list[DiscoveredDevice]:
        loop = asyncio.get_running_loop()
        transport, proto = await loop.create_datagram_endpoint(
            _Collector, local_addr=("0.0.0.0", 0), allow_broadcast=True
        )
        try:
            payload = json.dumps({"method": "getPilot", "params": {}}).encode()
            for target in {local_broadcast(), "255.255.255.255"}:
                try:
                    transport.sendto(payload, (target, PORT))
                except OSError:
                    pass
            await asyncio.sleep(min(timeout_s, 3.0))
            replies = dict(proto.replies)
        finally:
            transport.close()

        found: list[DiscoveredDevice] = []
        for ip, message in replies.items():
            result = message.get("result") or {}
            mac = str(result.get("mac", "")).lower()
            if not mac:
                continue
            module = ""
            try:
                module = str((await self._request(ip, "getSystemConfig")).get("moduleName", ""))
            except Exception:
                pass
            found.append(
                DiscoveredDevice(
                    provider=self.key, native_id=mac, name=f"WiZ {mac[-4:]}", manufacturer=self.manufacturer,
                    model=module or None, address=ip, capabilities=capabilities_for(module),
                    metadata={"module": module},
                )
            )
        return found

    def _ip(self, device: DeviceRecord) -> str:
        if not device.address:
            raise DeviceOfflineError(f"{device.name} has no known address; run a scan")
        return device.address

    async def get_state(self, device: DeviceRecord) -> DeviceState:
        return parse_pilot(await self._request(self._ip(device), "getPilot"))

    async def execute(self, device: DeviceRecord, command: Command) -> DeviceState | None:
        ip = self._ip(device)
        result = await self._request(ip, "setPilot", pilot_params(command))
        if result.get("success") is False:
            raise ProviderError(f"{device.name} refused the command")
        await asyncio.sleep(0.3)
        return parse_pilot(await self._request(ip, "getPilot"))

"""WiZ (UDP JSON) and Shelly (HTTP) providers against simulated devices."""

import asyncio
import json

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import InvalidCommandError
from app.domain.models import RGB, Capability, DeviceRecord, SetBrightness, SetColor, SetPower
from app.providers.shelly_lan import provider as shelly_mod
from app.providers.shelly_lan.provider import ShellyLanProvider, parse_identity
from app.providers.wiz_lan import provider as wiz_mod
from app.providers.wiz_lan.provider import WizLanProvider, capabilities_for, parse_pilot


def _record(provider: str, caps, metadata=None) -> DeviceRecord:
    return DeviceRecord(
        id=f"{provider}-x", provider=provider, native_id="x", name="Test", manufacturer="m",
        address="127.0.0.1", capabilities=caps, metadata=metadata or {},
    )


def test_wiz_capabilities_and_parsing() -> None:
    rgb = capabilities_for("ESP01_SHRGB1C_31")
    assert rgb.has(Capability.COLOR_RGB) and rgb.color_temp_range.min_k == 2200
    assert not capabilities_for("ESP56_SHTW3_01").has(Capability.COLOR_RGB)
    assert capabilities_for("ESP10_SOCKET_06").supported == frozenset({Capability.POWER})
    state = parse_pilot({"state": True, "dimming": 40, "r": 255, "g": 0, "b": 10})
    assert state.power and state.brightness == 40 and state.color == RGB(r=255, g=0, b=10)
    assert parse_pilot({"state": False, "dimming": 50, "temp": 2700}).color_temp_k == 2700


class FakeWiz(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.pilot = {"mac": "a8bb50aabbcc", "state": False, "dimming": 50, "temp": 2700}

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data, addr) -> None:
        msg = json.loads(data)
        if msg["method"] == "getPilot":
            result = dict(self.pilot)
        elif msg["method"] == "setPilot":
            p = msg["params"]
            if "r" in p:
                self.pilot.pop("temp", None)
            self.pilot.update(p)
            result = {"success": True}
        else:
            result = {"moduleName": "ESP01_SHRGB1C_31"}
        self.transport.sendto(json.dumps({"method": msg["method"], "result": result}).encode(), addr)


async def test_wiz_execute_confirms_by_reading_back(monkeypatch) -> None:
    loop = asyncio.get_running_loop()
    transport, fake = await loop.create_datagram_endpoint(FakeWiz, local_addr=("127.0.0.1", 0))
    monkeypatch.setattr(wiz_mod, "PORT", transport.get_extra_info("sockname")[1])
    try:
        provider = WizLanProvider(Settings(_env_file=None))
        device = _record("wiz_lan", capabilities_for("ESP01_SHRGB1C_31"))
        state = await provider.execute(device, SetColor(color=RGB(r=0, g=0, b=255)))
        assert state.power and state.color == RGB(r=0, g=0, b=255)
        state = await provider.execute(device, SetBrightness(brightness=3))
        assert state.brightness == 10  # WiZ minimum
    finally:
        transport.close()


def _shelly_handler(gen: int):
    state = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        q = dict(request.url.params)
        if path == "/shelly":
            info = {"mac": "AABBCCDDEEFF", "gen": 2, "model": "SNSW-001X16EU", "name": "Λάμπα γραφείου"} if gen == 2 \
                else {"type": "SHSW-1", "mac": "AABBCCDDEEFF"}
            return httpx.Response(200, json=info)
        if path == "/rpc/Switch.Set":
            state["on"] = q["on"] == "true"
            return httpx.Response(200, json={"was_on": not state["on"]})
        if path == "/rpc/Switch.GetStatus":
            return httpx.Response(200, json={"id": 0, "output": state["on"]})
        if path == "/relay/0":
            if "turn" in q:
                state["on"] = q["turn"] == "on"
            return httpx.Response(200, json={"ison": state["on"]})
        return httpx.Response(404)

    return handler


@pytest.mark.parametrize("gen", [1, 2])
async def test_shelly_discover_and_switch(monkeypatch, gen: int) -> None:
    monkeypatch.setattr(shelly_mod, "local_subnet_ips", lambda: ["127.0.0.1"])
    http = httpx.AsyncClient(transport=httpx.MockTransport(_shelly_handler(gen)))
    provider = ShellyLanProvider(Settings(_env_file=None), http=http)
    found = await provider.discover(1)
    assert len(found) == 1 and found[0].native_id == "aabbccddeeff" and found[0].metadata["gen"] == gen
    device = _record("shelly_lan", found[0].capabilities, found[0].metadata)
    assert (await provider.execute(device, SetPower(on=True))).power is True
    assert (await provider.get_state(device)).power is True
    with pytest.raises(InvalidCommandError):
        await provider.execute(device, SetBrightness(brightness=10))
    await provider.stop()


def test_parse_identity_rejects_non_shelly() -> None:
    assert parse_identity("1.2.3.4", {"hello": 1}) is None

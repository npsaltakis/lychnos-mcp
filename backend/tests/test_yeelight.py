"""Yeelight provider against a simulated bulb that follows the official Inter-Operation spec."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.config import Settings
from app.core.errors import DeviceOfflineError, DeviceTimeoutError, ProviderError
from app.domain.models import (
    RGB,
    Capability,
    DeviceRecord,
    DeviceState,
    LightMode,
    SetBrightness,
    SetColor,
    SetColorTemperature,
    SetPower,
    make_device_id,
)
from app.providers.yeelight_lan import protocol
from app.providers.yeelight_lan.provider import YeelightLanProvider

# Discovery answer as printed in the Yeelight Inter-Operation Specification
SPEC_RESPONSE = (
    "HTTP/1.1 200 OK\r\n"
    "Cache-Control: max-age=3600\r\n"
    "Date: \r\n"
    "Ext: \r\n"
    "Location: yeelight://192.168.1.239:55443\r\n"
    "Server: POSIX UPnP/1.0 YGLC/1\r\n"
    "id: 0x000000000015243f\r\n"
    "model: color\r\n"
    "fw_ver: 18\r\n"
    "support: get_prop set_default set_power toggle set_bright start_cf stop_cf set_scene cron_add cron_get "
    "cron_del set_ct_abx set_rgb\r\n"
    "power: on\r\n"
    "bright: 100\r\n"
    "color_mode: 2\r\n"
    "ct: 4000\r\n"
    "rgb: 16711680\r\n"
    "hue: 100\r\n"
    "sat: 35\r\n"
    "name: my_bulb\r\n"
).encode()


def test_parse_discovery_response_from_spec() -> None:
    headers = protocol.parse_search_response(SPEC_RESPONSE)
    assert headers is not None
    assert headers["id"] == "0x000000000015243f"
    assert protocol.location_host_port(headers["location"]) == ("192.168.1.239", 55443)
    caps = protocol.capabilities_for(headers["model"], headers["support"])
    assert caps.supported == frozenset(Capability)
    assert caps.color_temp_range is not None and caps.color_temp_range.min_k == 1700
    assert caps.supports_push and caps.supports_state_query
    assert protocol.friendly_name(headers) == "my_bulb"
    assert protocol.parse_search_response(b"HTTP/1.1 200 OK\r\nServer: other\r\n") is None


def test_white_bulb_has_no_colour() -> None:
    caps = protocol.capabilities_for("mono", "get_prop set_power toggle set_bright")
    assert caps.supported == frozenset({Capability.POWER, Capability.BRIGHTNESS})
    assert caps.color_temp_range is None


def test_props_to_state() -> None:
    state, full = protocol.state_from_props({"power": "on", "bright": "40", "color_mode": "1", "rgb": "16711935"})
    assert full and state == DeviceState(power=True, brightness=40, color=RGB(r=255, g=0, b=255), mode=LightMode.COLOR)
    state, _ = protocol.state_from_props({"power": "off", "bright": "10", "color_mode": "2", "ct": "2700"})
    assert state.mode is LightMode.WHITE and state.color_temp_k == 2700 and state.power is False
    state, full = protocol.state_from_props({"bright": "55"})
    assert not full and state.brightness == 55


def test_commands_turn_on_first_when_off() -> None:
    off = DeviceState(power=False)
    assert protocol.commands_for(SetColor(color=RGB(r=255, g=0, b=0)), off) == [
        ("set_power", ["on", "smooth", 300]),
        ("set_rgb", [0xFF0000, "smooth", 300]),
    ]
    on = DeviceState(power=True)
    assert protocol.commands_for(SetColorTemperature(kelvin=3000), on) == [("set_ct_abx", [3000, "smooth", 300])]


# ----------------------------------------------------------------------------- simulated bulb over TCP


class SimulatedBulb:
    def __init__(self, supports_rgb: bool = True) -> None:
        self.props = {"power": "off", "bright": "50", "ct": "4000", "rgb": "16777215", "hue": "0", "sat": "0", "color_mode": "2"}
        self.supports_rgb = supports_rgb
        self.writers: list[asyncio.StreamWriter] = []
        self.received: list[tuple[str, list]] = []
        self.server: asyncio.base_events.Server | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for w in self.writers:
            w.close()
        if self.server:
            self.server.close()

    async def notify(self, changes: dict[str, str]) -> None:
        self.props.update(changes)
        line = (json.dumps({"method": "props", "params": changes}) + "\r\n").encode()
        for w in self.writers:
            w.write(line)
            await w.drain()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        while line := await reader.readline():
            req = json.loads(line)
            method, params = req["method"], req["params"]
            self.received.append((method, params))
            if method == "get_prop":
                answer = {"id": req["id"], "result": [self.props.get(p, "") for p in params]}
            elif method == "set_rgb" and not self.supports_rgb:
                answer = {"id": req["id"], "error": {"code": -1, "message": "unsupported method"}}
            elif method != "set_power" and self.props["power"] != "on":
                answer = {"id": req["id"], "error": {"code": -1, "message": "client quota exceeded"}}
            else:
                changes = {
                    "set_power": lambda p: {"power": p[0]},
                    "set_bright": lambda p: {"bright": str(p[0])},
                    "set_rgb": lambda p: {"rgb": str(p[0]), "color_mode": "1"},
                    "set_ct_abx": lambda p: {"ct": str(p[0]), "color_mode": "2"},
                }[method](params)
                answer = {"id": req["id"], "result": ["ok"]}
                writer.write((json.dumps(answer) + "\r\n").encode())
                await writer.drain()
                await self.notify(changes)
                continue
            writer.write((json.dumps(answer) + "\r\n").encode())
            await writer.drain()


class Sink:
    def __init__(self) -> None:
        self.reports: list[tuple[DeviceState, bool]] = []

    async def state_reported(self, provider, native_id, state, *, partial=False) -> None:
        self.reports.append((state, partial))

    async def address_changed(self, *args) -> None: ...


@pytest.fixture
async def bulb():
    b = SimulatedBulb()
    port = await b.start()
    b.port = port
    yield b
    await b.stop()


def record(port: int, address: str = "127.0.0.1") -> DeviceRecord:
    return DeviceRecord(
        id=make_device_id("yeelight_lan", "0x15243f"),
        provider="yeelight_lan",
        native_id="0x15243f",
        name="Yeelight",
        manufacturer="Yeelight",
        model="color",
        address=address,
        registered=True,
        capabilities=protocol.capabilities_for("color", "get_prop set_power set_bright set_rgb set_ct_abx"),
        metadata={"port": port},
    )


async def test_commands_are_confirmed_by_readback(tmp_path, bulb: SimulatedBulb) -> None:
    provider = YeelightLanProvider(Settings(_env_file=None, data_dir=tmp_path))
    sink = Sink()
    await provider.start(sink)
    dev = record(bulb.port)
    try:
        state = await provider.execute(dev, SetColor(color=RGB(r=128, g=0, b=128)))
        # off -> turned on first, then colour
        methods = [m for m, _ in bulb.received if m != "get_prop"]
        assert methods == ["set_power", "set_rgb"]
        assert state is not None and state.power is True and state.color == RGB(r=128, g=0, b=128)

        state = await provider.execute(dev, SetBrightness(brightness=35))
        assert state is not None and state.brightness == 35

        state = await provider.execute(dev, SetColorTemperature(kelvin=2700))
        assert state is not None and state.mode is LightMode.WHITE and state.color_temp_k == 2700

        state = await provider.execute(dev, SetPower(on=False))
        assert state is not None and state.power is False
        assert sink.reports  # push notifications were forwarded
    finally:
        await provider.stop()


async def test_push_from_other_app_is_forwarded(tmp_path, bulb: SimulatedBulb) -> None:
    provider = YeelightLanProvider(Settings(_env_file=None, data_dir=tmp_path))
    sink = Sink()
    await provider.start(sink)
    try:
        await provider.get_state(record(bulb.port))  # opens the connection
        await bulb.notify({"power": "on", "bright": "80"})
        await asyncio.sleep(0.05)
        state, partial = sink.reports[-1]
        assert partial and state.power is True and state.brightness == 80
    finally:
        await provider.stop()


async def test_device_error_and_offline(tmp_path, bulb: SimulatedBulb) -> None:
    provider = YeelightLanProvider(Settings(_env_file=None, data_dir=tmp_path))
    await provider.start(Sink())
    try:
        bulb.supports_rgb = False
        bulb.props["power"] = "on"
        with pytest.raises(ProviderError, match="unsupported method"):
            await provider.execute(record(bulb.port), SetColor(color=RGB(r=1, g=2, b=3)))
        # Windows retries refused localhost connects for ~2 s, so either error is acceptable.
        with pytest.raises((DeviceOfflineError, DeviceTimeoutError)):
            await provider.get_state(record(1, address="127.0.0.1"))
    finally:
        await provider.stop()

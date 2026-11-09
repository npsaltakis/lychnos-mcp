from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import DeviceOfflineError, ProviderError
from app.domain.models import (
    RGB,
    DeviceRecord,
    DeviceState,
    LightMode,
    SetBrightness,
    SetColor,
    SetColorTemperature,
    SetPower,
    make_device_id,
)
from app.integrations.ewelink_setup import sign
from app.providers.ewelink_lan import crypto
from app.providers.ewelink_lan.keystore import EwelinkDeviceInfo, KeyStore
from app.providers.ewelink_lan.models import SPECS, ct_to_kelvin, kelvin_to_ct
from app.providers.ewelink_lan.provider import EwelinkLanProvider
from app.providers.govee_lan import protocol as govee

# ============================================================================ Govee protocol


def test_govee_command_encoding_matches_official_docs() -> None:
    assert json.loads(govee.command_request(SetPower(on=False))) == {"msg": {"cmd": "turn", "data": {"value": 0}}}
    assert json.loads(govee.command_request(SetBrightness(brightness=20))) == {
        "msg": {"cmd": "brightness", "data": {"value": 20}}
    }
    assert json.loads(govee.command_request(SetColor(color=RGB(r=0, g=12, b=8)))) == {
        "msg": {"cmd": "colorwc", "data": {"color": {"r": 0, "g": 12, "b": 8}, "colorTemInKelvin": 0}}
    }
    ct = json.loads(govee.command_request(SetColorTemperature(kelvin=7200)))
    assert ct["msg"]["data"]["colorTemInKelvin"] == 7200
    assert json.loads(govee.scan_request()) == {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}


def test_govee_status_parsing() -> None:
    # Real reply captured from the H6056 on 2026-11-07
    raw = b'{"msg":{"cmd":"devStatus","data":{"onOff":1,"brightness":100,"color":{"r":255,"g":127,"b":0},"colorTemInKelvin":0}}}'
    cmd, data = govee.parse_message(raw)
    assert cmd == "devStatus"
    state = govee.parse_status(data)
    assert state == DeviceState(power=True, brightness=100, color=RGB(r=255, g=127, b=0), mode=LightMode.COLOR)

    white = govee.parse_status({"onOff": 0, "brightness": 35, "color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": 4000})
    assert white == DeviceState(power=False, brightness=35, color_temp_k=4000, mode=LightMode.WHITE)


def test_govee_ignores_garbage() -> None:
    assert govee.parse_message(b"not json") is None
    assert govee.parse_message(b'{"foo": 1}') is None


# ============================================================================ eWeLink crypto / API signing


def test_ewelink_crypto_roundtrip() -> None:
    data, iv = crypto.encrypt({"switch": "on"}, "devicekey-123")
    assert crypto.decrypt(data, iv, "devicekey-123") == {"switch": "on"}
    with pytest.raises(Exception):
        crypto.decrypt(data, iv, "wrong-key")


def test_ewelink_signature_matches_official_example() -> None:
    # Example from the eWeLink OAuth 2.0 documentation
    assert sign("abc", "ABC_123") == "v1+mfNY2ukxswM8sZOTg99srZsVnUVv9DGXeav1096M="


# ============================================================================ eWeLink UIID 104 (B05-BL)

B05 = SPECS[104]


def test_b05_parse_color_and_white() -> None:
    state, full = B05.parse({"switch": "on", "ltype": "color", "color": {"br": 40, "r": 10, "g": 20, "b": 30}})
    assert full
    assert state == DeviceState(power=True, brightness=40, color=RGB(r=10, g=20, b=30), mode=LightMode.COLOR)

    state, full = B05.parse({"switch": "off", "ltype": "white", "white": {"br": 70, "ct": 255}})
    assert full
    assert state == DeviceState(power=False, brightness=70, color_temp_k=6500, mode=LightMode.WHITE)

    state, full = B05.parse({"switch": "on"})
    assert not full and state.power is True and state.brightness is None


def test_b05_encode_keeps_current_mode_for_brightness() -> None:
    white = DeviceState(power=True, brightness=50, color_temp_k=6500, mode=LightMode.WHITE)
    assert B05.encode(SetBrightness(brightness=20), white) == {"ltype": "white", "white": {"br": 20, "ct": 255}}

    color = DeviceState(power=True, brightness=50, color=RGB(r=1, g=2, b=3), mode=LightMode.COLOR)
    assert B05.encode(SetBrightness(brightness=20), color) == {
        "ltype": "color",
        "color": {"br": 20, "r": 1, "g": 2, "b": 3},
    }
    assert B05.encode(SetPower(on=False), color) == {"switch": "off"}


def test_ct_conversion_roundtrip() -> None:
    for kelvin in (2000, 3000, 4500, 6500):
        assert abs(ct_to_kelvin(kelvin_to_ct(kelvin)) - kelvin) <= 20


# ============================================================================ eWeLink provider with a simulated bulb

DEVICEID = "1000abcdef"
DEVICEKEY = "test-devicekey"


class MemoryKeyStore(KeyStore):
    def __init__(self, keys: dict[str, str], infos: list[EwelinkDeviceInfo]) -> None:
        self._keys = keys
        self._infos = {i.deviceid: i for i in infos}

    def load(self) -> dict[str, EwelinkDeviceInfo]:
        return dict(self._infos)

    def get_devicekey(self, deviceid: str) -> str | None:  # type: ignore[override]
        return self._keys.get(deviceid)


class SimulatedBulb:
    """Decrypts requests like the real bulb and re-announces its state over 'mDNS'."""

    def __init__(self, provider: EwelinkLanProvider, announce: bool = True) -> None:
        self.provider = provider
        self.announce = announce
        self.params = {"switch": "off", "ltype": "white", "white": {"br": 100, "ct": 255}}
        self.seq = 1
        self.requests: list[tuple[str, dict]] = []

    def txt(self) -> dict[str, str]:
        data, iv = crypto.encrypt(self.params, DEVICEKEY)
        return {"id": DEVICEID, "type": "light", "encrypt": "true", "iv": iv, "seq": str(self.seq), "data1": data}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        params = crypto.decrypt(body["data"], body["iv"], DEVICEKEY)
        self.requests.append((request.url.path, params))
        self.params = {**self.params, **params}
        self.seq += 1
        if self.announce:
            await self.provider._handle_announcement(DEVICEID, "192.168.1.50:8081", self.txt())
        return httpx.Response(200, json={"seq": self.seq, "error": 0})


class RecordingSink:
    def __init__(self) -> None:
        self.reports: list[tuple[DeviceState, bool]] = []

    async def state_reported(self, provider, native_id, state, *, partial=False) -> None:
        self.reports.append((state, partial))

    async def address_changed(self, provider, native_id, address) -> None: ...


async def make_provider(tmp_path, *, announce: bool = True, key: bool = True):
    store = MemoryKeyStore(
        {DEVICEID: DEVICEKEY} if key else {},
        [EwelinkDeviceInfo(deviceid=DEVICEID, name="Sonoff Bulb", uiid=104, model="B05-BL")],
    )
    provider = EwelinkLanProvider(Settings(_env_file=None, data_dir=tmp_path), keystore=store)
    provider._info = store.load()
    bulb = SimulatedBulb(provider, announce=announce)
    provider._http = httpx.AsyncClient(transport=httpx.MockTransport(bulb.handler))
    sink = RecordingSink()
    provider._sink = sink
    await provider._handle_announcement(DEVICEID, "192.168.1.50:8081", bulb.txt())
    record = DeviceRecord(
        id=make_device_id("ewelink_lan", DEVICEID),
        provider="ewelink_lan",
        native_id=DEVICEID,
        name="Sonoff Bulb",
        manufacturer="Sonoff",
        model="B05-BL",
        address="192.168.1.50",
        registered=True,
        capabilities=SPECS[104].capabilities,
    )
    return provider, bulb, sink, record


async def test_ewelink_announcement_decrypts_state(tmp_path) -> None:
    provider, _, sink, record = await make_provider(tmp_path)
    state = await provider.get_state(record)
    assert state == DeviceState(power=False, brightness=100, color_temp_k=6500, mode=LightMode.WHITE)
    assert sink.reports[-1] == (state, False)
    found = await provider.discover(0.01)
    assert found[0].capabilities == SPECS[104].capabilities
    assert found[0].metadata["has_devicekey"] is True


async def test_ewelink_command_confirmed_by_announcement(tmp_path) -> None:
    provider, bulb, _, record = await make_provider(tmp_path)
    state = await provider.execute(record, SetColor(color=RGB(r=128, g=0, b=128)))
    assert bulb.requests[-1] == ("/zeroconf/dimmable", {"ltype": "color", "color": {"br": 100, "r": 128, "g": 0, "b": 128}})
    assert state is not None and state.color == RGB(r=128, g=0, b=128) and state.mode is LightMode.COLOR

    state = await provider.execute(record, SetPower(on=True))
    assert bulb.requests[-1] == ("/zeroconf/switch", {"switch": "on"})
    assert state is not None and state.power is True


async def test_ewelink_unconfirmed_without_announcement(tmp_path, monkeypatch) -> None:
    import app.providers.ewelink_lan.provider as mod

    monkeypatch.setattr(mod, "CONFIRM_WAIT_S", 0.05)
    provider, _, _, record = await make_provider(tmp_path, announce=False)
    assert await provider.execute(record, SetPower(on=True)) is None


async def test_ewelink_requires_key_and_presence(tmp_path) -> None:
    provider, _, _, record = await make_provider(tmp_path, key=False)
    with pytest.raises(ProviderError, match="key setup"):
        await provider.execute(record, SetPower(on=True))
    found = await provider.discover(0.01)
    assert found[0].capabilities.supported == frozenset()
    assert "setup_required" in found[0].metadata

    provider._seen.clear()
    with pytest.raises(DeviceOfflineError):
        await provider.get_state(record)


def test_ewelink_uiid_136_uses_ct_0_to_100() -> None:
    from app.domain.models import DeviceState, SetColorTemperature
    from app.providers.ewelink_lan.models import spec_for

    spec = spec_for(136)
    assert spec is not None
    assert spec.encode(SetColorTemperature(kelvin=6500), DeviceState(brightness=50)) == {
        "ltype": "white", "white": {"br": 50, "ct": 100}
    }
    state, full = spec.parse({"switch": "on", "ltype": "white", "white": {"br": 40, "ct": 0}})
    assert full and state.color_temp_k == 2000 and state.brightness == 40

"""Govee RGBIC segments: packet encoding (verified on an H6056) and the gradient API."""

import base64
import json

from app.services.device_service import blend
from app.domain.models import RGB
from app.providers.govee_lan import protocol


def test_segment_packet_matches_hardware_format() -> None:
    msg = json.loads(protocol.segments_request([RGB(r=255, g=0, b=0)] * 6 + [RGB(r=0, g=0, b=255)] * 6))
    assert msg["msg"]["cmd"] == "ptReal"
    packets = [base64.b64decode(p) for p in msg["msg"]["data"]["command"]]
    assert len(packets) == 2 and all(len(p) == 20 for p in packets)
    red = packets[0]
    assert list(red[:7]) == [0x33, 0x05, 0x15, 0x01, 255, 0, 0]
    assert red[12] == 0b111111 and red[13] == 0  # segments 1-6
    assert packets[1][12] == 0b11000000 and packets[1][13] == 0b1111  # segments 7-12
    x = 0
    for byte in red[:19]:
        x ^= byte
    assert red[19] == x


def test_blend() -> None:
    out = blend([RGB(r=0, g=0, b=0), RGB(r=250, g=0, b=100)], 6)
    assert out[0] == RGB(r=0, g=0, b=0) and out[-1] == RGB(r=250, g=0, b=100) and out[3].r == 150
    assert blend([RGB(r=1, g=2, b=3)], 4) == [RGB(r=1, g=2, b=3)] * 4



async def test_cloud_music_picks_style_from_options() -> None:
    import httpx

    from app.core.secrets import MemorySecretStore
    from app.providers.govee_lan.cloud import SECRET_NAME, GoveeCloud

    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Govee-API-Key"] == "k"
        if request.url.path.endswith("/user/devices"):
            return httpx.Response(200, json={"code": 200, "data": [{
                "sku": "H6056", "device": "DF:F9:D3:2E:43:46:36:2F", "deviceName": "Bars",
                "capabilities": [{"type": "devices.capabilities.music_setting", "instance": "musicMode",
                                  "parameters": {"fields": [{"fieldName": "musicMode", "options": [
                                      {"name": "Energic", "value": 5}, {"name": "Rhythm", "value": 3}]}]}}],
            }]})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 200, "msg": "success"})

    store = MemorySecretStore()
    store.set(SECRET_NAME, "k")
    cloud = GoveeCloud(store, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await cloud.set_music("H6056", "df:f9:d3:2e:43:46:36:2f", "rhythm", 90)
    value = sent[0]["payload"]["capability"]["value"]
    assert value == {"musicMode": 3, "sensitivity": 90, "autoColor": 1}


async def test_cloud_music_without_key_explains() -> None:
    import pytest

    from app.core.errors import InvalidCommandError
    from app.core.secrets import MemorySecretStore
    from app.providers.govee_lan.cloud import GoveeCloud

    with pytest.raises(InvalidCommandError, match="govee_key"):
        await GoveeCloud(MemorySecretStore()).set_music("H6056", "x", "energic", 50)

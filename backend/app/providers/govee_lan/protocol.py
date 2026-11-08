"""Govee LAN API message encoding/decoding (pure functions, no I/O).

Reference: https://app-h5.govee.com/user-manual/wlan-guide
- scan:      multicast 239.255.255.250:4001, replies to client UDP :4002
- commands:  unicast device:4003 (turn, brightness, colorwc) — not acknowledged
- devStatus: unicast device:4003, reply to client UDP :4002
"""

from __future__ import annotations

import base64
import json
from typing import Any

from app.domain.models import (
    RGB,
    Capabilities,
    Capability,
    ColorTempRange,
    Command,
    DeviceState,
    LightMode,
    SetBrightness,
    SetColor,
    SetColorTemperature,
    SetPower,
)

MULTICAST_GROUP = "239.255.255.250"
SCAN_PORT = 4001
LISTEN_PORT = 4002
CONTROL_PORT = 4003

# The LAN API documents these ranges for every supported model.
LAN_COLOR_TEMP_RANGE = ColorTempRange(min_k=2000, max_k=9000)
LAN_CAPABILITIES = Capabilities(
    supported=frozenset(
        {Capability.POWER, Capability.BRIGHTNESS, Capability.COLOR_RGB, Capability.COLOR_TEMP}
    ),
    brightness_min=1,
    color_temp_range=LAN_COLOR_TEMP_RANGE,
    supports_state_query=True,
    supports_push=False,
)

# Friendly model names for SKUs verified in this installation.
KNOWN_MODELS: dict[str, str] = {
    "H6056": "RGBICWW Light Bars",
}


# Colour segments ("RGBIC") per model, verified on hardware. H6056: two bars × 6 segments.
KNOWN_SEGMENTS: dict[str, int] = {
    "H6056": 12,
}


# Music mode: not available over LAN or plain BLE packets on these models (tested on an H6056),
# so it goes through Govee's official cloud API (needs an API key; see cloud.py).
MUSIC_MODELS = {"H6056"}


def _segment_packet(r: int, g: int, b: int, mask: int) -> str:
    """One 20-byte BLE-style packet (sent through the LAN 'ptReal' command), base64-encoded."""
    body = [0x33, 0x05, 0x15, 0x01, r, g, b, 0, 0, 0, 0, 0, mask & 0xFF, (mask >> 8) & 0xFF] + [0] * 5
    checksum = 0
    for byte in body:
        checksum ^= byte
    return base64.b64encode(bytes([*body, checksum])).decode()


def segments_request(colors: list[RGB]) -> bytes:
    """Set each segment's colour; segments sharing a colour go in one packet."""
    masks: dict[tuple[int, int, int], int] = {}
    for index, c in enumerate(colors):
        masks[(c.r, c.g, c.b)] = masks.get((c.r, c.g, c.b), 0) | (1 << index)
    packets = [_segment_packet(*rgb, mask) for rgb, mask in masks.items()]
    return _encode("ptReal", {"command": packets})


def _encode(cmd: str, data: dict[str, Any]) -> bytes:
    return json.dumps({"msg": {"cmd": cmd, "data": data}}, separators=(",", ":")).encode()


def scan_request() -> bytes:
    return _encode("scan", {"account_topic": "reserve"})


def status_request() -> bytes:
    return _encode("devStatus", {})


def command_request(command: Command) -> bytes:
    match command:
        case SetPower(on=on):
            return _encode("turn", {"value": 1 if on else 0})
        case SetBrightness(brightness=value):
            return _encode("brightness", {"value": value})
        case SetColor(color=color):
            return _encode(
                "colorwc",
                {"color": {"r": color.r, "g": color.g, "b": color.b}, "colorTemInKelvin": 0},
            )
        case SetColorTemperature(kelvin=kelvin):
            return _encode(
                "colorwc", {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": kelvin}
            )
    raise ValueError(f"Unsupported command: {command!r}")


def parse_message(payload: bytes) -> tuple[str, dict[str, Any]] | None:
    """Return (cmd, data) or None if the datagram is not a Govee message."""
    try:
        message = json.loads(payload.decode("utf-8"))
        msg = message["msg"]
        return str(msg["cmd"]), dict(msg.get("data") or {})
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None


def parse_status(data: dict[str, Any]) -> DeviceState:
    """Convert a devStatus payload into a full DeviceState snapshot."""
    kelvin = int(data.get("colorTemInKelvin") or 0)
    color = data.get("color") or {}
    brightness = data.get("brightness")
    return DeviceState(
        power=bool(data["onOff"]) if "onOff" in data else None,
        brightness=max(0, min(100, int(brightness))) if brightness is not None else None,
        color=RGB(r=int(color.get("r", 0)), g=int(color.get("g", 0)), b=int(color.get("b", 0)))
        if kelvin == 0 and color
        else None,
        color_temp_k=kelvin if kelvin > 0 else None,
        mode=LightMode.WHITE if kelvin > 0 else LightMode.COLOR,
    )

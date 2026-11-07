"""Govee LAN API message encoding/decoding (pure functions, no I/O).

Reference: https://app-h5.govee.com/user-manual/wlan-guide
- scan:      multicast 239.255.255.250:4001, replies to client UDP :4002
- commands:  unicast device:4003 (turn, brightness, colorwc) — not acknowledged
- devStatus: unicast device:4003, reply to client UDP :4002
"""

from __future__ import annotations

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

"""Yeelight LAN protocol (Xiaomi/Yeelight lights), pure functions.

Reference: Yeelight WiFi Light Inter-Operation Specification
(https://www.yeelight.com/download/Yeelight_Inter-Operation_Spec.pdf)

- Discovery: SSDP-like ``M-SEARCH`` to 239.255.255.250:1982 with ``ST: wifi_bulb``; the light answers
  unicast with headers (id, model, support, power, bright, color_mode, ct, rgb, hue, sat, name).
- Control: TCP (default port 55443), one JSON object per line:
  ``{"id":1,"method":"set_power","params":["on","smooth",300]}`` → ``{"id":1,"result":["ok"]}``.
- Push: every connected client receives ``{"method":"props","params":{...}}`` when the state changes.
- Requirement: "LAN Control" must be enabled for the light in the Yeelight app.
"""

from __future__ import annotations

import colorsys
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
SSDP_PORT = 1982
DEFAULT_PORT = 55443
EFFECT = "smooth"
DURATION_MS = 300
STATE_PROPS = ["power", "bright", "ct", "rgb", "hue", "sat", "color_mode"]

SEARCH_REQUEST = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {MULTICAST_GROUP}:{SSDP_PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "ST: wifi_bulb\r\n"
).encode()

# Colour-temperature ranges per model family. Unknown models get the narrower, safe range.
CT_RANGES: dict[str, ColorTempRange] = {
    "color": ColorTempRange(min_k=1700, max_k=6500),
    "stripe": ColorTempRange(min_k=1700, max_k=6500),
    "strip": ColorTempRange(min_k=1700, max_k=6500),
    "bslamp": ColorTempRange(min_k=1700, max_k=6500),
    "lamp": ColorTempRange(min_k=2700, max_k=6500),
    "ceiling": ColorTempRange(min_k=2700, max_k=6500),
    "ct_bulb": ColorTempRange(min_k=2700, max_k=6500),
    "desklamp": ColorTempRange(min_k=2700, max_k=6500),
}
DEFAULT_CT_RANGE = ColorTempRange(min_k=2700, max_k=6500)

MODEL_NAMES = {
    "mono": "White bulb",
    "color": "Color bulb",
    "stripe": "Light strip",
    "strip": "Light strip",
    "ceiling": "Ceiling light",
    "bslamp": "Bedside lamp",
    "ct_bulb": "White CT bulb",
    "desklamp": "Desk lamp",
}


def parse_search_response(payload: bytes) -> dict[str, str] | None:
    """Return lower-cased headers of a discovery answer/advertisement, or None."""
    try:
        text = payload.decode("utf-8", errors="replace")
    except Exception:
        return None
    lines = text.split("\r\n")
    if not lines or not (lines[0].startswith("HTTP/1.1 200") or lines[0].startswith("NOTIFY")):
        return None
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    if "id" not in headers or not headers.get("location", "").startswith("yeelight://"):
        return None
    return headers


def location_host_port(location: str) -> tuple[str, int]:
    host_port = location.removeprefix("yeelight://").strip("/")
    host, _, port = host_port.partition(":")
    return host, int(port) if port else DEFAULT_PORT


def _family(model: str) -> str:
    model = model.lower()
    for family in CT_RANGES:
        if model.startswith(family):
            return family
    return model


def capabilities_for(model: str, support: str) -> Capabilities:
    """Capabilities come from the methods the light itself says it supports."""
    methods = set(support.split())
    supported: set[Capability] = set()
    if "set_power" in methods:
        supported.add(Capability.POWER)
    if "set_bright" in methods:
        supported.add(Capability.BRIGHTNESS)
    if "set_rgb" in methods:
        supported.add(Capability.COLOR_RGB)
    ct_range = None
    if "set_ct_abx" in methods:
        supported.add(Capability.COLOR_TEMP)
        ct_range = CT_RANGES.get(_family(model), DEFAULT_CT_RANGE)
    return Capabilities(
        supported=frozenset(supported),
        brightness_min=1,
        color_temp_range=ct_range,
        supports_state_query="get_prop" in methods,
        supports_push=True,
    )


def friendly_name(headers: dict[str, str]) -> str:
    if headers.get("name"):
        return headers["name"]
    model = headers.get("model", "")
    return f"Yeelight {MODEL_NAMES.get(_family(model), model or 'light')}"


def _int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def state_from_props(props: dict[str, Any]) -> tuple[DeviceState, bool]:
    """Convert Yeelight properties (strings) to a DeviceState. Returns (state, is_full)."""
    values: dict[str, Any] = {}
    if props.get("power") in ("on", "off"):
        values["power"] = props["power"] == "on"
    bright = _int(props.get("bright"))
    if bright is not None:
        values["brightness"] = max(0, min(100, bright))
    mode = _int(props.get("color_mode"))
    if mode == 2:
        values["mode"] = LightMode.WHITE
        ct = _int(props.get("ct"))
        if ct:
            values["color_temp_k"] = ct
    elif mode in (1, 3):
        values["mode"] = LightMode.COLOR
        if mode == 1 and (rgb := _int(props.get("rgb"))) is not None:
            values["color"] = RGB(r=(rgb >> 16) & 0xFF, g=(rgb >> 8) & 0xFF, b=rgb & 0xFF)
        elif mode == 3:
            hue, sat = _int(props.get("hue")), _int(props.get("sat"))
            if hue is not None and sat is not None:
                r, g, b = colorsys.hsv_to_rgb(hue / 359, sat / 100, 1.0)
                values["color"] = RGB(r=round(r * 255), g=round(g * 255), b=round(b * 255))
    full = "power" in values and "mode" in values
    return DeviceState(**values), full


def commands_for(command: Command, current: DeviceState) -> list[tuple[str, list[Any]]]:
    """Yeelight methods to send. Colour/brightness are only accepted while the light is on,
    so a light that is off is switched on first (as other apps do)."""
    turn_on_first: list[tuple[str, list[Any]]] = (
        [] if current.power is True else [("set_power", ["on", EFFECT, DURATION_MS])]
    )
    match command:
        case SetPower(on=on):
            return [("set_power", ["on" if on else "off", EFFECT, DURATION_MS])]
        case SetBrightness(brightness=value):
            return [*turn_on_first, ("set_bright", [value, EFFECT, DURATION_MS])]
        case SetColor(color=c):
            rgb = (c.r << 16) | (c.g << 8) | c.b
            # pure black is not a valid Yeelight colour
            return [*turn_on_first, ("set_rgb", [max(rgb, 1), EFFECT, DURATION_MS])]
        case SetColorTemperature(kelvin=kelvin):
            return [*turn_on_first, ("set_ct_abx", [kelvin, EFFECT, DURATION_MS])]
    raise ValueError(f"Unsupported command: {command!r}")


def encode_request(request_id: int, method: str, params: list[Any]) -> bytes:
    return (json.dumps({"id": request_id, "method": method, "params": params}, separators=(",", ":")) + "\r\n").encode()


def decode_line(line: bytes) -> dict[str, Any] | None:
    try:
        message = json.loads(line.decode("utf-8").strip())
    except (ValueError, UnicodeDecodeError):
        return None
    return message if isinstance(message, dict) else None

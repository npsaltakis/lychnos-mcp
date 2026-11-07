"""Per-UIID device specifications: capabilities, state parsing and command encoding.

Only UIIDs verified for this installation are listed. Unknown UIIDs get no capabilities,
so nothing is offered for them until they are verified and added here.

UIID 104 (Sonoff B05-BL / B05-B RGB+CCT bulbs), eWeLink parameters:
  {"switch": "on"|"off", "ltype": "color"|"white"|<effect>,
   "color": {"br": 1..100, "r": 0..255, "g": 0..255, "b": 0..255},
   "white": {"br": 1..100, "ct": 0..255}}      ct 0 = warmest, 255 = coldest
"""

from __future__ import annotations

from dataclasses import dataclass
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

B05_CT_RANGE = ColorTempRange(min_k=2000, max_k=6500)
CT_RAW_MAX = 255

NO_CAPABILITIES = Capabilities(supported=frozenset(), supports_state_query=False)


def kelvin_to_ct(kelvin: int, rng: ColorTempRange = B05_CT_RANGE) -> int:
    ratio = (kelvin - rng.min_k) / (rng.max_k - rng.min_k)
    return max(0, min(CT_RAW_MAX, round(ratio * CT_RAW_MAX)))


def ct_to_kelvin(ct: int, rng: ColorTempRange = B05_CT_RANGE) -> int:
    ratio = max(0, min(CT_RAW_MAX, ct)) / CT_RAW_MAX
    return round(rng.min_k + ratio * (rng.max_k - rng.min_k))


@dataclass(frozen=True)
class UiidSpec:
    uiid: int
    name: str
    capabilities: Capabilities

    def parse(self, params: dict[str, Any]) -> tuple[DeviceState, bool]:
        """Return (state, is_full_snapshot)."""
        raise NotImplementedError

    def encode(self, command: Command, current: DeviceState) -> dict[str, Any]:
        raise NotImplementedError


class B05Spec(UiidSpec):
    def parse(self, params: dict[str, Any]) -> tuple[DeviceState, bool]:
        power = None
        if "switch" in params:
            power = params["switch"] == "on"
        ltype = params.get("ltype")
        values: dict[str, Any] = {"power": power}
        if ltype == "white":
            white = params.get("white") or {}
            values["mode"] = LightMode.WHITE
            if "br" in white:
                values["brightness"] = int(white["br"])
            if "ct" in white:
                values["color_temp_k"] = ct_to_kelvin(int(white["ct"]))
        elif ltype == "color":
            color = params.get("color") or {}
            values["mode"] = LightMode.COLOR
            if "br" in color:
                values["brightness"] = int(color["br"])
            if {"r", "g", "b"} <= color.keys():
                values["color"] = RGB(r=int(color["r"]), g=int(color["g"]), b=int(color["b"]))
        # Full snapshot only when both the switch and the active light mode are reported.
        full = "switch" in params and ltype in ("white", "color")
        return DeviceState(**values), full

    def encode(self, command: Command, current: DeviceState) -> dict[str, Any]:
        brightness = current.brightness or 100
        match command:
            case SetPower(on=on):
                return {"switch": "on" if on else "off"}
            case SetColor(color=c):
                return {"ltype": "color", "color": {"br": brightness, "r": c.r, "g": c.g, "b": c.b}}
            case SetColorTemperature(kelvin=k):
                return {"ltype": "white", "white": {"br": brightness, "ct": kelvin_to_ct(k)}}
            case SetBrightness(brightness=br):
                if current.mode is LightMode.WHITE:
                    ct = kelvin_to_ct(current.color_temp_k or B05_CT_RANGE.max_k)
                    return {"ltype": "white", "white": {"br": br, "ct": ct}}
                c = current.color or RGB(r=255, g=255, b=255)
                return {"ltype": "color", "color": {"br": br, "r": c.r, "g": c.g, "b": c.b}}
        raise ValueError(f"Unsupported command: {command!r}")


B05_CAPABILITIES = Capabilities(
    supported=frozenset(
        {Capability.POWER, Capability.BRIGHTNESS, Capability.COLOR_RGB, Capability.COLOR_TEMP}
    ),
    brightness_min=1,
    color_temp_range=B05_CT_RANGE,
    supports_state_query=True,  # state comes from the device's own mDNS announcements
    supports_push=True,
)

SPECS: dict[int, UiidSpec] = {
    104: B05Spec(uiid=104, name="RGB+CCT bulb", capabilities=B05_CAPABILITIES),
}


def spec_for(uiid: int | None) -> UiidSpec | None:
    return SPECS.get(uiid) if uiid is not None else None

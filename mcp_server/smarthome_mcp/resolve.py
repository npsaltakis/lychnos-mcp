"""Turning natural references ("the Govee light", "blue", "μωβ") into ids and values."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from smarthome_mcp.client import BackendError

ALL_WORDS = {"all", "all lights", "everything", "every light", "όλα", "ολα", "όλα τα φώτα", "ολα τα φωτα"}

# English and Greek colour names → hex
COLORS: dict[str, str] = {
    "red": "#ff0000", "κόκκινο": "#ff0000",
    "orange": "#ff7f00", "πορτοκαλί": "#ff7f00",
    "yellow": "#ffd200", "κίτρινο": "#ffd200",
    "green": "#00ff40", "πράσινο": "#00ff40",
    "lime": "#80ff00",
    "cyan": "#00dcff", "κυανό": "#00dcff", "γαλάζιο": "#00b4ff", "light blue": "#00b4ff",
    "blue": "#003cff", "μπλε": "#003cff",
    "purple": "#8c00ff", "μωβ": "#8c00ff", "violet": "#8c00ff", "βιολετί": "#8c00ff",
    "magenta": "#ff00ff", "φούξια": "#ff00ff",
    "pink": "#ff0096", "ροζ": "#ff0096",
    "white": "#ffffff", "λευκό": "#ffffff", "άσπρο": "#ffffff",
    "warm white": "#ffb46b", "ζεστό λευκό": "#ffb46b",
    "teal": "#00c8a0",
    "gold": "#ffb000", "χρυσό": "#ffb000",
}

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
_RGB = re.compile(r"^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$", re.I)


def normalize(text: str) -> str:
    """Lowercase and strip accents so 'Μωβ' == 'μωβ' and 'Λάμπα' == 'λαμπα'."""
    decomposed = unicodedata.normalize("NFD", text.strip().lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


_COLORS_NORMALIZED = {normalize(k): v for k, v in COLORS.items()}


def parse_color(value: str) -> dict[str, int]:
    text = value.strip()
    if match := _HEX.match(text):
        raw = match.group(1)
        return {"r": int(raw[0:2], 16), "g": int(raw[2:4], 16), "b": int(raw[4:6], 16)}
    if match := _RGB.match(text):
        r, g, b = (int(x) for x in match.groups())
        if max(r, g, b) <= 255:
            return {"r": r, "g": g, "b": b}
    named = _COLORS_NORMALIZED.get(normalize(text))
    if named:
        return parse_color(named)
    raise BackendError(
        "unknown_color",
        f"Unknown colour {value!r}. Use a hex value like #8000ff, rgb(128,0,255) or a name: "
        + ", ".join(sorted(k for k in COLORS if k.isascii())),
    )


def is_all(reference: str) -> bool:
    return normalize(reference) in {normalize(w) for w in ALL_WORDS}


def find_room(rooms: list[dict[str, Any]], reference: str, *, exact_only: bool = False) -> dict[str, Any] | None:
    """Room by id or name (accent/case-insensitive); exact match first, then a unique partial match."""
    ref = normalize(reference)
    for word in ("the ", "room ", "το ", "η ", "δωματιο "):
        if ref.startswith(word):
            ref = ref[len(word):]
    exact = [r for r in rooms if r["id"] == reference or normalize(r["name"]) == ref]
    if exact or exact_only:
        return exact[0] if exact else None
    partial = [r for r in rooms if ref and (ref in normalize(r["name"]) or normalize(r["name"]) in ref)]
    return partial[0] if len(partial) == 1 else None


WEEKDAYS: dict[str, list[int]] = {
    "mon": [0], "monday": [0], "δευ": [0], "δευτερα": [0],
    "tue": [1], "tuesday": [1], "τρι": [1], "τριτη": [1],
    "wed": [2], "wednesday": [2], "τετ": [2], "τεταρτη": [2],
    "thu": [3], "thursday": [3], "πεμ": [3], "πεμπτη": [3],
    "fri": [4], "friday": [4], "παρ": [4], "παρασκευη": [4],
    "sat": [5], "saturday": [5], "σαβ": [5], "σαββατο": [5],
    "sun": [6], "sunday": [6], "κυρ": [6], "κυριακη": [6],
    "weekdays": [0, 1, 2, 3, 4], "καθημερινες": [0, 1, 2, 3, 4],
    "weekend": [5, 6], "σαββατοκυριακο": [5, 6],
    "every day": list(range(7)), "daily": list(range(7)), "καθε μερα": list(range(7)),
}


def parse_days(days: list[str] | None) -> list[int]:
    if not days:
        return list(range(7))
    result: set[int] = set()
    for item in days:
        key = normalize(str(item))
        if key.isdigit() and 0 <= int(key) <= 6:
            result.add(int(key))
        elif key in WEEKDAYS:
            result.update(WEEKDAYS[key])
        else:
            raise BackendError(
                "invalid_days",
                f"Unknown day {item!r}. Use mon…sun, weekdays, weekend or 0 (Monday) … 6 (Sunday).",
            )
    return sorted(result)


def resolve_device(views: list[dict[str, Any]], reference: str) -> dict[str, Any]:
    """Find exactly one device by id, name, manufacturer or model (accent/case-insensitive)."""
    ref = normalize(reference)
    ref_words = [w for w in re.split(r"\W+", ref) if w and w not in {"the", "light", "lamp", "bulb", "η", "το", "λαμπα", "φως"}]

    def keys(view: dict[str, Any]) -> list[str]:
        d = view["device"]
        return [normalize(str(x)) for x in (d["id"], d["name"], d["manufacturer"], d.get("model") or "", d["provider"])]

    exact = [v for v in views if ref in keys(v)[:2]]
    candidates = exact or [
        v for v in views if ref_words and all(any(w in k for k in keys(v)) for w in ref_words)
    ]
    if not candidates:
        names = ", ".join(f"{v['device']['name']} ({v['device']['manufacturer']})" for v in views) or "none"
        raise BackendError("device_not_found", f"No device matches {reference!r}. Known devices: {names}")
    registered = [v for v in candidates if v["device"]["registered"]]
    if not registered:
        raise BackendError(
            "device_not_registered",
            f"{candidates[0]['device']['name']} exists but is not enabled for control. "
            "Enable it on the dashboard's Devices page.",
        )
    if len(registered) > 1:
        names = ", ".join(v["device"]["name"] for v in registered)
        raise BackendError("ambiguous_device", f"{reference!r} matches several devices: {names}. Be more specific.")
    return registered[0]

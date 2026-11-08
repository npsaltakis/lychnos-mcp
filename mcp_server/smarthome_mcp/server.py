"""Smart Home MCP server (stdio).

Exposes light-control tools to Claude Desktop / Claude Code. Every call goes through the
local backend REST API (same validation, state and history as the dashboard). Nothing is
sent to devices directly from here.

Run: ``python -m smarthome_mcp``  (logs go to stderr; stdout is the MCP channel)
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from smarthome_mcp.client import BackendClient, BackendError
from smarthome_mcp.resolve import find_room, is_all, normalize, parse_color, parse_days, resolve_device

log = logging.getLogger("smarthome_mcp")

INSTRUCTIONS = """\
Controls the user's real smart lights through their local Smart Home backend.
- Use list_devices first if you are unsure which lights exist or what they support.
- Pass device names as the user says them ("Govee", "Sonoff", "desk light"); use "all" for every light.
- Room names ("living room", "Σαλόνι") work wherever a device is expected; list_rooms shows them.
- Automations run on the home computer's local time; use list_automations / create_automation.
- Every command result has an `outcome`. Only say a change happened if outcome is "confirmed".
  "unconfirmed" = sent but the device cannot confirm; "mismatch" = the device reports something else.
- Only controls a device supports are allowed (see `capabilities`).
- If `state_is_last_known_only` is true the device is offline/unknown: describe its state as "last known".
"""

OUTCOME_NOTES = {
    "confirmed": "The device reported the requested state.",
    "unconfirmed": "Sent, but this device cannot confirm it. Do not claim it definitely changed.",
    "mismatch": "The device reports a different state than requested.",
    "failed": "The command was not applied.",
}

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
CONTROL = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

mcp = MCPServer(name="smart-home", title="Lychnos smart lights", instructions=INSTRUCTIONS, version="0.1.0")
_client: BackendClient | None = None


def client() -> BackendClient:
    global _client
    if _client is None:
        _client = BackendClient()
    return _client


# ---------------------------------------------------------------------------- formatting


def _hex(color: dict[str, int] | None) -> str | None:
    return f"#{color['r']:02x}{color['g']:02x}{color['b']:02x}" if color else None


def summarize_state(state: dict[str, Any]) -> dict[str, Any]:
    power = state.get("power")
    return {
        "power": "on" if power is True else "off" if power is False else "unknown",
        "brightness_percent": state.get("brightness"),
        "mode": state.get("mode"),
        "color_hex": _hex(state.get("color")),
        "color_temperature_k": state.get("color_temp_k"),
    }


def summarize_device(view: dict[str, Any]) -> dict[str, Any]:
    device, runtime = view["device"], view["runtime"]
    caps = device["capabilities"]
    return {
        "id": device["id"],
        "name": device["name"],
        "manufacturer": device["manufacturer"],
        "model": device.get("model"),
        "registered": device["registered"],
        "availability": runtime["availability"],
        "state": summarize_state(runtime["state"]),
        # Offline/unknown devices only have their last known state; it may no longer be true.
        "state_is_last_known_only": runtime["availability"] != "online",
        "state_confirmed_at": runtime.get("state_confirmed_at"),
        "pending_command": (runtime.get("pending") or {}).get("command"),
        "capabilities": caps["supported"],
        "color_temperature_range_k": caps.get("color_temp_range"),
        "last_error": (runtime.get("last_error") or {}).get("message"),
    }


def summarize_result(result: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    outcome = result["outcome"]
    return {
        "device": names.get(result["device_id"], result["device_id"]),
        "device_id": result["device_id"],
        "outcome": outcome,
        "verified_by_device": outcome == "confirmed",
        "note": OUTCOME_NOTES[outcome],
        "reported_state": summarize_state(result["state"]),
        "error": (result.get("error") or {}).get("message"),
        "duration_ms": result.get("duration_ms"),
    }


def summarize_batch(batch: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    return {
        "outcome": batch["outcome"],
        "results": [summarize_result(r, names) for r in batch["results"]],
        "skipped": [
            {"device": names.get(s["device_id"], s["device_id"]), "reason": s["reason"]} for s in batch["skipped"]
        ],
    }


# Failure codes where the device never received the command (connection refused, not reachable).
NOT_DELIVERED = {"device_offline", "provider_unavailable"}


def _tool_error(err: BackendError) -> ToolError:
    result = err.details.get("result") if isinstance(err.details, dict) else None
    if not result:
        return ToolError(str(err))
    if err.code in NOT_DELIVERED:
        return ToolError(f"[{err.code}] {err.message}. The command was NOT applied.")
    # Timeouts and protocol errors: the command may have reached the device without an answer.
    return ToolError(
        f"[{err.code}] {err.message}. The device did not confirm the command: it may or may not have "
        "been applied. Do not claim it worked; use get_device_state to check."
    )


# ---------------------------------------------------------------------------- command plumbing


async def _names() -> tuple[list[dict[str, Any]], dict[str, str]]:
    views = await client().devices()
    return views, {v["device"]["id"]: v["device"]["name"] for v in views}


async def _resolve_target(target: str) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], dict[str, str]]:
    """Return (kind, item, views, names) where kind is 'all', 'room' or 'device'."""
    views, names = await _names()
    if is_all(target):
        return "all", None, views, names
    rooms = await client().rooms()
    room = find_room(rooms, target, exact_only=True)
    if room:
        return "room", room, views, names
    try:
        return "device", resolve_device(views, target), views, names
    except BackendError as err:
        if err.code == "device_not_found" and (room := find_room(rooms, target)):
            return "room", room, views, names
        if err.code == "device_not_found" and rooms:
            err.message += ". Rooms: " + ", ".join(r["name"] for r in rooms)
        raise


async def _send(device: str, command: dict[str, Any]) -> dict[str, Any]:
    try:
        kind, item, _views, names = await _resolve_target(device)
        if kind == "all":
            return {"target": "all", **summarize_batch(await client().group_command(command), names)}
        if kind == "room":
            assert item is not None
            batch = await client().room_command(item["id"], command)
            return {"target": f"room {item['name']}", **summarize_batch(batch, names)}
        assert item is not None
        result = await client().command(item["device"]["id"], command)
        return summarize_result(result, names)
    except BackendError as err:
        raise _tool_error(err) from None


# ---------------------------------------------------------------------------- tools


@mcp.tool(annotations=READ)
async def list_devices(include_unregistered: bool = False) -> dict[str, Any]:
    """List the lights with their live state (on/off, brightness, colour), availability and supported controls.

    Use this to answer questions like "which lights are on?". Only registered devices can be controlled.
    """
    try:
        views, _ = await _names()
        room_names = {r["id"]: r["name"] for r in await client().rooms()}
    except BackendError as err:
        raise _tool_error(err) from None
    devices = [
        {**summarize_device(v), "room": room_names.get(v["device"].get("room_id") or "")}
        for v in views
        if include_unregistered or v["device"]["registered"]
    ]
    return {"devices": devices, "count": len(devices)}


@mcp.tool(annotations=READ)
async def list_rooms() -> dict[str, Any]:
    """List rooms with their lights and how many are on. Room names can be used wherever a device is expected."""
    try:
        rooms = await client().rooms()
        views, _ = await _names()
    except BackendError as err:
        raise _tool_error(err) from None
    result = []
    for room in rooms:
        members = [v for v in views if v["device"].get("room_id") == room["id"] and v["device"]["registered"]]
        result.append(
            {
                "id": room["id"],
                "name": room["name"],
                "lights": [v["device"]["name"] for v in members],
                "on": sum(1 for v in members if v["runtime"]["state"].get("power") is True),
            }
        )
    unassigned = [v["device"]["name"] for v in views if v["device"]["registered"] and not v["device"].get("room_id")]
    return {"rooms": result, "lights_without_room": unassigned}


@mcp.tool(annotations=READ)
async def get_device_state(device: str, refresh: bool = True) -> dict[str, Any]:
    """Get one light's state. With refresh=true the state is read from the device itself.

    Args:
        device: Device name, manufacturer (e.g. "Govee") or id.
        refresh: Read from the hardware instead of the backend cache.
    """
    try:
        views, _ = await _names()
        view = resolve_device(views, device)
        runtime = await client().state(view["device"]["id"], refresh)
    except BackendError as err:
        raise _tool_error(err) from None
    return summarize_device({"device": view["device"], "runtime": runtime})


@mcp.tool(annotations=CONTROL)
async def turn_on(device: str) -> dict[str, Any]:
    """Turn a light on. `device` may also be a room name, or "all" for every light."""
    return await _send(device, {"type": "set_power", "on": True})


@mcp.tool(annotations=CONTROL)
async def turn_off(device: str) -> dict[str, Any]:
    """Turn a light off. `device` may also be a room name, or "all" for every light."""
    return await _send(device, {"type": "set_power", "on": False})


@mcp.tool(annotations=CONTROL)
async def set_brightness(device: str, percent: int = Field(ge=1, le=100)) -> dict[str, Any]:
    """Set brightness in percent (1-100). `device` may be a light, a room name or "all"."""
    return await _send(device, {"type": "set_brightness", "brightness": percent})


@mcp.tool(annotations=CONTROL)
async def set_color(device: str, color: str) -> dict[str, Any]:
    """Set an RGB colour on a light, a room or "all" (lights without colour are skipped).

    Args:
        device: Device name/id, room name or "all".
        color: Hex ("#8000ff"), "rgb(128,0,255)" or a colour name in English or Greek
            (red, orange, yellow, green, cyan, blue, purple, pink, white, μπλε, μωβ, ...).
    """
    try:
        rgb = parse_color(color)
    except BackendError as err:
        raise _tool_error(err) from None
    return await _send(device, {"type": "set_color", "color": rgb})


@mcp.tool(annotations=CONTROL)
async def start_effect(
    effect: Literal["rainbow", "breathe", "candle"], device: str = "all"
) -> dict[str, Any]:
    """Start a light effect that keeps running until stopped or the light gets another command.

    Args:
        effect: "rainbow" (colour slowly cycles), "breathe" (brightness rises and falls) or
            "candle" (warm flickering flame).
        device: Light name, room name or "all".
    """
    try:
        kind, item, _views, names = await _resolve_target(device)
        target: dict[str, Any] = {"kind": kind}
        if kind == "room":
            target["id"] = item["id"]  # type: ignore[index]
        elif kind == "device":
            target["id"] = item["device"]["id"]  # type: ignore[index]
        run = await client().request("POST", "/effects", {"effect": effect, "target": target})
    except BackendError as err:
        raise _tool_error(err) from None
    return {"effect": run["effect"], "lights": [names.get(i, i) for i in run["device_ids"]]}


@mcp.tool(annotations=CONTROL)
async def set_gradient(device: str, colors: list[str], same_on_each_bar: bool = True) -> dict[str, Any]:
    """Colour an RGBIC light (e.g. the Govee light bars) segment by segment with a gradient.

    Args:
        device: The light's name (must be a single light with colour segments).
        colors: Two or more colours blended along the light, e.g. ["orange", "purple"] or ["#ff0000", "#0000ff"].
        same_on_each_bar: For lights made of two bars, repeat the gradient on each bar.
    """
    try:
        kind, item, _views, names = await _resolve_target(device)
        if kind != "device":
            raise BackendError("not_a_device", "Gradients work on one light at a time; name the light")
        rgb = [parse_color(c) for c in colors]
        device_id = item["device"]["id"]  # type: ignore[index]
        await client().request(
            "POST", f"/devices/{device_id}/segments",
            {"colors": rgb, "mode": "per_bar" if same_on_each_bar else "gradient"},
        )
    except BackendError as err:
        raise _tool_error(err) from None
    return {"light": names.get(device_id, device_id), "gradient": colors, "confirmed": False,
            "note": "The light cannot report segment colours back"}


@mcp.tool(annotations=CONTROL)
async def music_mode(
    device: str,
    style: str = "",
    sensitivity: int = Field(80, ge=0, le=100),
) -> dict[str, Any]:
    """Make a light follow the sound in the room with its own microphone (e.g. the Govee light bars).

    It stays in music mode until the light gets any other command (colour, scene, effect…).

    Args:
        style: A style the light offers (for the Govee H6056: vivid, strike, rhythm, vibrate, beat, torch,
            rainbowcircle, shiny). Empty = the light's first style. Unknown styles fall back to the first.
    """
    try:
        kind, item, _views, names = await _resolve_target(device)
        if kind != "device":
            raise BackendError("not_a_device", "Music mode works on one light at a time; name the light")
        device_id = item["device"]["id"]  # type: ignore[index]
        await client().request("POST", f"/devices/{device_id}/music", {"style": style, "sensitivity": sensitivity})
    except BackendError as err:
        raise _tool_error(err) from None
    return {"light": names.get(device_id, device_id), "music": style, "sensitivity": sensitivity, "confirmed": False}


@mcp.tool(annotations=CONTROL)
async def stop_effects() -> dict[str, Any]:
    """Stop every running light effect (the lights stay as they are at that moment)."""
    try:
        running = await client().request("POST", "/effects/stop", {})
    except BackendError as err:
        raise _tool_error(err) from None
    return {"stopped": True, "still_running": running}


@mcp.tool(annotations=CONTROL)
async def set_color_temperature(device: str, kelvin: int = Field(ge=1000, le=12000)) -> dict[str, Any]:
    """Set white colour temperature in Kelvin (e.g. 2700 warm, 4000 neutral, 6500 cool).

    The allowed range depends on the device (see list_devices → color_temperature_range_k).
    """
    return await _send(device, {"type": "set_color_temperature", "kelvin": kelvin})


@mcp.tool(annotations=READ)
async def list_scenes() -> dict[str, Any]:
    """List saved lighting scenes."""
    try:
        scenes = await client().scenes()
        _, names = await _names()
    except BackendError as err:
        raise _tool_error(err) from None
    return {
        "scenes": [
            {
                "id": s["id"],
                "name": s["name"],
                "description": s["description"],
                "actions": [
                    {**{k: v for k, v in a.items() if v is not None and k != "device_id"}, "device": names.get(a["device_id"], a["device_id"])}
                    for a in s["actions"]
                ],
            }
            for s in scenes
        ]
    }


@mcp.tool(annotations=CONTROL)
async def activate_scene(scene: str) -> dict[str, Any]:
    """Activate a saved scene by name or id."""
    try:
        scenes = await client().scenes()
        ref = normalize(scene)
        match = next((s for s in scenes if s["id"] == scene or normalize(s["name"]) == ref), None)
        match = match or next((s for s in scenes if ref in normalize(s["name"])), None)
        if match is None:
            available = ", ".join(s["name"] for s in scenes) or "none"
            raise BackendError("scene_not_found", f"No scene named {scene!r}. Available: {available}")
        _, names = await _names()
        batch = await client().activate_scene(match["id"])
    except BackendError as err:
        raise _tool_error(err) from None
    return {"scene": match["name"], **summarize_batch(batch, names)}


class SceneActionInput(BaseModel):
    device: str = Field(description="Device name, manufacturer or id")
    power: bool | None = Field(None, description="false = off (no other values allowed); true = on")
    brightness: int | None = Field(None, ge=1, le=100, description="Percent")
    color: str | None = Field(None, description="Hex, rgb() or colour name. Not together with color_temperature_k")
    color_temperature_k: int | None = Field(None, ge=1000, le=12000)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
async def create_scene(name: str, actions: list[SceneActionInput], description: str = "") -> dict[str, Any]:
    """Save a new lighting scene (does not activate it; call activate_scene afterwards if asked).

    Example "movie night": [{"device": "Govee", "color": "purple", "brightness": 20},
    {"device": "Sonoff", "power": false}].
    """
    try:
        views, _ = await _names()
        payload_actions = []
        for action in actions:
            view = resolve_device(views, action.device)
            payload_actions.append(
                {
                    "device_id": view["device"]["id"],
                    "power": action.power,
                    "brightness": action.brightness,
                    "color": parse_color(action.color) if action.color else None,
                    "color_temp_k": action.color_temperature_k,
                }
            )
        scene = await client().create_scene({"name": name, "description": description, "actions": payload_actions})
    except BackendError as err:
        raise _tool_error(err) from None
    return {"created": True, "id": scene["id"], "name": scene["name"], "actions": len(scene["actions"])}


# ---------------------------------------------------------------------------- automations

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def summarize_automation(a: dict[str, Any], names: dict[str, str], rooms: dict[str, str], scenes: dict[str, str]) -> dict[str, Any]:
    trigger, action = a["trigger"], a["action"]
    if trigger["type"] == "time":
        when = trigger["time"]
    else:
        offset = trigger.get("offset_min", 0)
        when = trigger["event"] + (f" {offset:+d} min" if offset else "")
    days = trigger.get("days", list(range(7)))
    if action["type"] == "scene":
        what = f"scene {scenes.get(action['scene_id'], action['scene_id'])}"
    else:
        target = action["target"]
        target_name = (
            "all lights" if target["kind"] == "all"
            else f"room {rooms.get(target['id'], target['id'])}" if target["kind"] == "room"
            else names.get(target["id"], target["id"])
        )
        if action["type"] == "fade":
            what = f"wake-up fade to {action['to_brightness']}% over {action['duration_min']} min on {target_name}"
        elif action["type"] == "fade_out":
            what = f"sleep fade-out over {action['duration_min']} min on {target_name}"
        else:
            what = f"{action['command']} on {target_name}"
    return {
        "id": a["id"],
        "name": a["name"],
        "enabled": a["enabled"],
        "when": when,
        "days": "every day" if len(days) == 7 else ", ".join(DAY_NAMES[d] for d in days),
        "action": what,
        "next_run_at": a.get("next_run_at"),
        "last_run_at": a.get("last_run_at"),
        "last_result": a.get("last_result"),
    }


async def _automation_context() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    _, names = await _names()
    rooms = {r["id"]: r["name"] for r in await client().rooms()}
    scenes = {s["id"]: s["name"] for s in await client().scenes()}
    return names, rooms, scenes


@mcp.tool(annotations=READ)
async def get_weight(days: int = Field(30, ge=1, le=3650)) -> dict[str, Any]:
    """The user's weight from their Bluetooth bathroom scale: latest weighing and the history of the last `days` days.

    Times are UTC (ISO 8601); weights in kg. Returns enabled=false if the scale is not set up in Settings.
    """
    try:
        status = await client().scale()
        rows = await client().weighings(days) if status["settings"]["enabled"] else []
    except BackendError as err:
        raise _tool_error(err) from None
    out: dict[str, Any] = {"enabled": status["settings"]["enabled"], "latest": status.get("latest"), "weighings": [
        {"ts": r["ts"], "kg": r["kg"]} for r in rows
    ]}
    if len(rows) >= 2:
        out["change_kg"] = round(rows[-1]["kg"] - rows[0]["kg"], 1)
    return out


@mcp.tool(annotations=READ)
async def list_automations() -> dict[str, Any]:
    """List automations (schedules) with when they run next and their last result. Times are local time."""
    try:
        automations = await client().automations()
        names, rooms, scenes = await _automation_context()
    except BackendError as err:
        raise _tool_error(err) from None
    return {"automations": [summarize_automation(a, names, rooms, scenes) for a in automations]}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
async def create_automation(
    name: str,
    when: str,
    action: Literal[
        "scene", "turn_on", "turn_off", "set_color", "set_brightness", "set_color_temperature", "wake_up_fade", "sleep_fade"
    ],
    target: str = "all",
    scene: str | None = None,
    color: str | None = None,
    brightness: int | None = Field(None, ge=1, le=100),
    kelvin: int | None = Field(None, ge=1000, le=12000),
    offset_minutes: int = Field(0, ge=-240, le=240),
    days: list[str] | None = None,
    fade_minutes: int = Field(15, ge=1, le=120),
    cloudy_earlier_minutes: int = 0,
) -> dict[str, Any]:
    """Create a scheduled automation (local time of the home computer).

    Args:
        name: Short name, e.g. "Evening lights".
        when: "HH:MM" (24h), "sunrise" or "sunset". Sunrise/sunset need the location set in the dashboard.
        action: What to do. "scene" needs `scene`; "set_color" needs `color`; "set_brightness" needs
            `brightness`; "set_color_temperature" needs `kelvin`; "wake_up_fade" switches the lights on at
            1 % and raises them to `brightness` (default 100) over `fade_minutes`, optionally in `kelvin` white;
            "sleep_fade" dims the lights that are on from their current brightness and switches them off
            after `fade_minutes`.
        target: Light name, room name or "all" (ignored for "scene").
        offset_minutes: For sunrise/sunset: minutes before (negative) or after (positive).
        cloudy_earlier_minutes: For sunset: run this much earlier when the forecast is cloudy.
        days: e.g. ["weekdays"], ["weekend"], ["mon", "fri"]; default every day.

    Examples: "every day at sunset turn on the living room" → when="sunset", action="turn_on",
    target="living room"; "wake me up at 07:00 on weekdays" → when="07:00", action="wake_up_fade",
    target="bedroom", days=["weekdays"].
    """
    try:
        if when.lower() in ("sunrise", "sunset", "ανατολη", "δυση", "ανατολή", "δύση"):
            event = "sunrise" if when.lower() in ("sunrise", "ανατολη", "ανατολή") else "sunset"
            trigger: dict[str, Any] = {"type": "sun", "event": event, "offset_min": offset_minutes,
                                       "cloudy_earlier_min": cloudy_earlier_minutes if event == "sunset" else 0}
        else:
            trigger = {"type": "time", "time": when.strip()}
        trigger["days"] = parse_days(days)

        if action == "scene":
            if not scene:
                raise BackendError("missing_scene", "action 'scene' needs the scene name")
            scenes = await client().scenes()
            ref = normalize(scene)
            match = next((s for s in scenes if normalize(s["name"]) == ref or s["id"] == scene), None)
            if match is None:
                raise BackendError("scene_not_found", f"No scene {scene!r}. Available: " + ", ".join(s["name"] for s in scenes))
            body_action: dict[str, Any] = {"type": "scene", "scene_id": match["id"]}
        else:
            kind, item, _views, _names_map = await _resolve_target(target)
            target_spec: dict[str, Any] = {"kind": kind}
            if kind == "room":
                target_spec["id"] = item["id"]  # type: ignore[index]
            elif kind == "device":
                target_spec["id"] = item["device"]["id"]  # type: ignore[index]
            if action == "sleep_fade":
                body_action = {"type": "fade_out", "target": target_spec, "duration_min": fade_minutes}
            elif action == "wake_up_fade":
                body_action = {
                    "type": "fade", "target": target_spec, "to_brightness": brightness or 100,
                    "duration_min": fade_minutes, "color_temp_k": kelvin,
                }
            else:
                command: dict[str, Any]
                if action in ("turn_on", "turn_off"):
                    command = {"type": "set_power", "on": action == "turn_on"}
                elif action == "set_color":
                    if not color:
                        raise BackendError("missing_color", "action 'set_color' needs `color`")
                    command = {"type": "set_color", "color": parse_color(color)}
                elif action == "set_brightness":
                    if brightness is None:
                        raise BackendError("missing_brightness", "action 'set_brightness' needs `brightness`")
                    command = {"type": "set_brightness", "brightness": brightness}
                else:
                    if kelvin is None:
                        raise BackendError("missing_kelvin", "action 'set_color_temperature' needs `kelvin`")
                    command = {"type": "set_color_temperature", "kelvin": kelvin}
                body_action = {"type": "command", "target": target_spec, "command": command}

        created = await client().create_automation({"name": name, "trigger": trigger, "action": body_action})
        names, rooms, scenes_map = await _automation_context()
    except BackendError as err:
        raise _tool_error(err) from None
    return {"created": True, **summarize_automation(created, names, rooms, scenes_map)}


async def _find_automation(automation: str) -> dict[str, Any]:
    automations = await client().automations()
    ref = normalize(automation)
    match = next((a for a in automations if a["id"] == automation or normalize(a["name"]) == ref), None)
    match = match or next((a for a in automations if ref in normalize(a["name"])), None)
    if match is None:
        raise BackendError(
            "automation_not_found",
            f"No automation {automation!r}. Available: " + (", ".join(a["name"] for a in automations) or "none"),
        )
    return match


@mcp.tool(annotations=CONTROL)
async def set_automation_enabled(automation: str, enabled: bool) -> dict[str, Any]:
    """Pause (enabled=false) or resume (enabled=true) an automation, by name or id."""
    try:
        match = await _find_automation(automation)
        updated = await client().set_automation_enabled(match["id"], enabled)
        names, rooms, scenes = await _automation_context()
    except BackendError as err:
        raise _tool_error(err) from None
    return summarize_automation(updated, names, rooms, scenes)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
async def delete_automation(automation: str) -> dict[str, Any]:
    """Delete an automation permanently, by name or id. Confirm with the user first."""
    try:
        match = await _find_automation(automation)
        await client().delete_automation(match["id"])
    except BackendError as err:
        raise _tool_error(err) from None
    return {"deleted": True, "name": match["name"]}


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run("stdio")


if __name__ == "__main__":
    main()

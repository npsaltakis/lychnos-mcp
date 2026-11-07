"""Smart Home MCP server (stdio).

Exposes light-control tools to Claude Desktop / Claude Code. Every call goes through the
local backend REST API (same validation, state and history as the dashboard). Nothing is
sent to devices directly from here.

Run: ``python -m smarthome_mcp``  (logs go to stderr; stdout is the MCP channel)
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from smarthome_mcp.client import BackendClient, BackendError
from smarthome_mcp.resolve import is_all, normalize, parse_color, resolve_device

log = logging.getLogger("smarthome_mcp")

INSTRUCTIONS = """\
Controls the user's real smart lights through their local Smart Home backend.
- Use list_devices first if you are unsure which lights exist or what they support.
- Pass device names as the user says them ("Govee", "Sonoff", "desk light"); use "all" for every light.
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


async def _send(device: str, command: dict[str, Any]) -> dict[str, Any]:
    try:
        views, names = await _names()
        if is_all(device):
            return {"target": "all", **summarize_batch(await client().group_command(command), names)}
        view = resolve_device(views, device)
        result = await client().command(view["device"]["id"], command)
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
    except BackendError as err:
        raise _tool_error(err) from None
    devices = [summarize_device(v) for v in views if include_unregistered or v["device"]["registered"]]
    return {"devices": devices, "count": len(devices)}


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
    """Turn a light on. Use device="all" for every light."""
    return await _send(device, {"type": "set_power", "on": True})


@mcp.tool(annotations=CONTROL)
async def turn_off(device: str) -> dict[str, Any]:
    """Turn a light off. Use device="all" for every light."""
    return await _send(device, {"type": "set_power", "on": False})


@mcp.tool(annotations=CONTROL)
async def set_brightness(device: str, percent: int = Field(ge=1, le=100)) -> dict[str, Any]:
    """Set brightness in percent (1-100). Use device="all" for every compatible light."""
    return await _send(device, {"type": "set_brightness", "brightness": percent})


@mcp.tool(annotations=CONTROL)
async def set_color(device: str, color: str) -> dict[str, Any]:
    """Set an RGB colour. Use device="all" for every light that supports colour.

    Args:
        device: Device name/id or "all".
        color: Hex ("#8000ff"), "rgb(128,0,255)" or a colour name in English or Greek
            (red, orange, yellow, green, cyan, blue, purple, pink, white, μπλε, μωβ, ...).
    """
    try:
        rgb = parse_color(color)
    except BackendError as err:
        raise _tool_error(err) from None
    return await _send(device, {"type": "set_color", "color": rgb})


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


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run("stdio")


if __name__ == "__main__":
    main()

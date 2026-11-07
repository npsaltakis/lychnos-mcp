"""HTTP client for the Smart Home Control backend (the only way the MCP server touches devices)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:3000"


class BackendError(Exception):
    """Error reported by (or about) the backend, with a stable code for the model."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


def data_dir() -> Path:
    """Same rules as the backend (app/core/config.py::_default_data_dir)."""
    if override := os.environ.get("SHC_DATA_DIR"):
        return Path(override)
    if os.name == "nt":
        return Path.home() / ".smarthome-control"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "SmartHomeControl"


def token_path() -> Path:
    return data_dir() / "api_token"


def load_token() -> str:
    token = os.environ.get("SHC_API_TOKEN")
    if token:
        return token.strip()
    path = token_path()
    if not path.is_file():
        raise BackendError(
            "backend_not_set_up",
            f"No API token at {path}. Start the Smart Home backend once to create it.",
        )
    return path.read_text(encoding="utf-8").strip()


class BackendClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self._base_url = (base_url or os.environ.get("SHC_API_URL") or DEFAULT_URL).rstrip("/")
        self._token = token
        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=timeout_s, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(self, method: str, path: str, body: Any = None) -> Any:
        token = self._token or load_token()  # re-read each call: the token file may be recreated
        try:
            response = await self._http.request(
                method,
                f"/api{path}",
                json=body,
                # X-SHC-Client: shown as the source of the change in the dashboard's history
                headers={"Authorization": f"Bearer {token}", "X-SHC-Client": "claude"},
            )
        except httpx.ConnectError:
            raise BackendError(
                "backend_not_running",
                f"The Smart Home backend is not running at {self._base_url}. "
                "Start it with scripts\\run-backend.ps1 and try again.",
            ) from None
        except httpx.TimeoutException:
            raise BackendError("backend_timeout", "The Smart Home backend did not answer in time.") from None

        if response.status_code == 204:
            return None
        try:
            payload = response.json()
        except ValueError:
            raise BackendError("bad_response", f"Unexpected answer from backend (HTTP {response.status_code}).") from None
        if response.is_error:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise BackendError(
                error.get("code", f"http_{response.status_code}"),
                error.get("message", "Request failed"),
                error.get("details", {}),
            )
        return payload

    # convenience wrappers
    async def devices(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/devices")

    async def state(self, device_id: str, refresh: bool) -> dict[str, Any]:
        return await self.request("GET", f"/devices/{device_id}/state?refresh={'true' if refresh else 'false'}")

    async def command(self, device_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", f"/devices/{device_id}/commands", command)

    async def group_command(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", "/groups/all/commands", command)

    async def scenes(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/scenes")

    async def create_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", "/scenes", scene)

    async def activate_scene(self, scene_id: str) -> dict[str, Any]:
        return await self.request("POST", f"/scenes/{scene_id}/activate")

    async def rooms(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/rooms")

    async def room_command(self, room_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", f"/rooms/{room_id}/commands", command)

    async def automations(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/automations")

    async def create_automation(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", "/automations", body)

    async def set_automation_enabled(self, automation_id: str, enabled: bool) -> dict[str, Any]:
        return await self.request("PATCH", f"/automations/{automation_id}", {"enabled": enabled})

    async def delete_automation(self, automation_id: str) -> None:
        await self.request("DELETE", f"/automations/{automation_id}")

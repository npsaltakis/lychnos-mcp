"""Govee's official cloud API, used only for what the LAN API cannot do (music mode).

Needs a free API key (Govee Home app → Profile → Settings → Apply for API Key), stored in the
OS keyring with ``python -m app.integrations.govee_key``. Everything else stays local.
Docs: https://developer.govee.com/reference/control-you-devices
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from app.core.errors import InvalidCommandError, ProviderError
from app.core.secrets import SecretStore

BASE = "https://openapi.api.govee.com/router/api/v1"
SECRET_NAME = "govee:api_key"
MUSIC = ("devices.capabilities.music_setting", "musicMode")
DEVICES_TTL_S = 3600


class GoveeCloud:
    def __init__(self, secrets: SecretStore, http: httpx.AsyncClient | None = None) -> None:
        self._secrets = secrets
        self._http = http
        self._devices: list[dict[str, Any]] | None = None
        self._devices_at = 0.0

    def _key(self) -> str:
        key = self._secrets.get(SECRET_NAME)
        if not key:
            raise InvalidCommandError(
                "Music mode needs a Govee API key. Run: python -m app.integrations.govee_key"
            )
        return key

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10)
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None, key: str | None = None) -> Any:
        headers = {"Govee-API-Key": key or self._key(), "Content-Type": "application/json"}
        try:
            response = await self._client().request(method, f"{BASE}{path}", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Govee cloud is not reachable ({exc.__class__.__name__})") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code == 401:
            raise ProviderError("Govee rejected the API key")
        if response.status_code == 429:
            raise ProviderError("Govee cloud rate limit reached; try again in a minute")
        if response.status_code >= 400 or data.get("code") not in (200, None):
            raise ProviderError(f"Govee cloud error: {data.get('msg') or data.get('message') or response.status_code}")
        return data

    async def devices(self, key: str | None = None, *, fresh: bool = False) -> list[dict[str, Any]]:
        if fresh or self._devices is None or time.monotonic() - self._devices_at > DEVICES_TTL_S:
            data = await self._call("GET", "/user/devices", key=key)
            self._devices = list(data.get("data") or [])
            self._devices_at = time.monotonic()
        return self._devices

    async def _device(self, device_id: str) -> dict[str, Any]:
        wanted = device_id.lower()
        for dev in await self.devices():
            if str(dev.get("device", "")).lower() == wanted:
                return dev
        raise InvalidCommandError("This light is not in your Govee account (or the key belongs to another account)")

    @staticmethod
    def music_options(dev: dict[str, Any]) -> dict[str, int]:
        """Music styles the light offers, e.g. {"energic": 5, "rhythm": 3, ...}."""
        for cap in dev.get("capabilities") or []:
            if (cap.get("type"), cap.get("instance")) != MUSIC:
                continue
            for field in (cap.get("parameters") or {}).get("fields") or []:
                if field.get("fieldName") == "musicMode":
                    return {str(o["name"]).lower(): int(o["value"]) for o in field.get("options") or []}
        return {}

    async def music_styles(self, device_id: str) -> list[str]:
        return list(self.music_options(await self._device(device_id)))

    async def set_music(self, sku: str, device_id: str, style: str, sensitivity: int) -> None:
        dev = await self._device(device_id)
        options = self.music_options(dev)
        if not options:
            raise InvalidCommandError("Govee does not offer music mode for this light in the cloud API")
        mode = options.get(style.lower())
        if mode is None:
            mode = next(iter(options.values()))
        await self._call("POST", "/device/control", {
            "requestId": uuid.uuid4().hex,
            "payload": {
                "sku": sku,
                "device": dev["device"],
                "capability": {
                    "type": MUSIC[0], "instance": MUSIC[1],
                    "value": {"musicMode": mode, "sensitivity": max(0, min(100, sensitivity)), "autoColor": 1},
                },
            },
        })

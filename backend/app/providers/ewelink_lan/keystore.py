"""Local storage of eWeLink device data obtained once from the eWeLink Open API.

- devicekey (secret)        -> OS keyring via ``keyring`` (Windows Credential Manager,
                               macOS Keychain, GNOME Keyring/KWallet on Linux)
- uiid / model / name       -> ``<data_dir>/ewelink_devices.json`` (not secret)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import keyring
from keyring.errors import KeyringError
from pydantic import BaseModel

log = logging.getLogger(__name__)

KEYRING_SERVICE = "SmartHomeControl"


class EwelinkDeviceInfo(BaseModel):
    deviceid: str
    name: str
    uiid: int | None = None
    model: str | None = None
    brand: str | None = None


class KeyStore:
    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "ewelink_devices.json"

    # ---- metadata
    def load(self) -> dict[str, EwelinkDeviceInfo]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return {d["deviceid"]: EwelinkDeviceInfo.model_validate(d) for d in raw}

    def save(self, devices: list[EwelinkDeviceInfo]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([d.model_dump() for d in devices], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---- secrets
    @staticmethod
    def get_devicekey(deviceid: str) -> str | None:
        try:
            return keyring.get_password(KEYRING_SERVICE, f"ewelink:{deviceid}")
        except KeyringError:
            log.exception("keyring read failed", extra={"ctx": {"deviceid": deviceid}})
            return None

    @staticmethod
    def set_devicekey(deviceid: str, devicekey: str) -> None:
        keyring.set_password(KEYRING_SERVICE, f"ewelink:{deviceid}", devicekey)

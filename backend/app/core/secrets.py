"""Small secrets store for integration credentials (Telegram token, SMTP password, Web Push key…).

Production: the OS keyring (Windows Credential Manager, GNOME Keyring/KWallet, macOS Keychain),
like the eWeLink device keys. Tests use ``MemorySecretStore`` so they never touch the real keyring.
"""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)

SERVICE = "SmartHomeControl"


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class KeyringSecretStore:
    def get(self, name: str) -> str | None:
        import keyring
        from keyring.errors import KeyringError

        try:
            return keyring.get_password(SERVICE, name)
        except KeyringError:
            log.exception("keyring read failed", extra={"ctx": {"name": name}})
            return None

    def set(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(SERVICE, name, value)

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(SERVICE, name)
        except PasswordDeleteError:
            pass


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)

"""Provider (adapter) interface. One implementation per manufacturer/protocol.

The core services only depend on this interface, so new brands are added by writing a
new provider package and registering it in ``registry.BUILTIN_PROVIDERS``.

Contract:
- ``get_state`` returns a full snapshot (fields the device does not report are ``None``).
- ``execute`` sends one command and returns the full state the device reports *afterwards*,
  or ``None`` when the device cannot report state.
  It must never fabricate a state it did not receive from the device.
- Failures are raised as ``DeviceOfflineError`` / ``DeviceTimeoutError`` / ``ProviderError``.
- Providers with push updates report them through the ``ProviderEventSink``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Protocol

from app.domain.models import Command, DeviceRecord, DeviceState, DiscoveredDevice


class ProviderEventSink(Protocol):
    async def state_reported(
        self, provider: str, native_id: str, state: DeviceState, *, partial: bool = False
    ) -> None:
        """The device announced its state (``partial=True`` if only changed fields are set)."""

    async def address_changed(self, provider: str, native_id: str, address: str) -> None:
        """The device was seen at a new network address."""


class DeviceProvider(ABC):
    key: ClassVar[str]
    manufacturer: ClassVar[str]

    async def start(self, sink: ProviderEventSink) -> None:  # noqa: B027 - optional hook
        """Start background listeners (e.g. mDNS). Default: nothing to start."""

    async def stop(self) -> None:  # noqa: B027 - optional hook
        """Stop background listeners and release sockets."""

    @abstractmethod
    async def discover(self, timeout_s: float) -> list[DiscoveredDevice]:
        """Find devices on the network. Must not change any device state."""

    @abstractmethod
    async def get_state(self, device: DeviceRecord) -> DeviceState:
        """Read the current state from the device."""

    @abstractmethod
    async def execute(self, device: DeviceRecord, command: Command) -> DeviceState | None:
        """Send a command; return the state reported by the device afterwards, or None."""

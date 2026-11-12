"""Provider registry: which device integrations are loaded."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable

from app.core.config import Settings
from app.core.errors import DeviceOfflineError, ProviderUnavailableError
from app.domain.models import RGB, Command, DeviceRecord, DeviceState, DiscoveredDevice
from app.providers.base import DeviceProvider, ProviderEventSink

log = logging.getLogger(__name__)

ProviderFactory = Callable[[Settings], DeviceProvider]
RETRY_LOAD_S = 60.0

def _govee_lan(settings: Settings) -> DeviceProvider:
    from app.providers.govee_lan import GoveeLanProvider

    return GoveeLanProvider(settings)


def _ewelink_lan(settings: Settings) -> DeviceProvider:
    from app.providers.ewelink_lan import EwelinkLanProvider

    return EwelinkLanProvider(settings)


def _yeelight_lan(settings: Settings) -> DeviceProvider:
    from app.providers.yeelight_lan import YeelightLanProvider

    return YeelightLanProvider(settings)


def _wiz_lan(settings: Settings) -> DeviceProvider:
    from app.providers.wiz_lan import WizLanProvider

    return WizLanProvider(settings)


def _shelly_lan(settings: Settings) -> DeviceProvider:
    from app.providers.shelly_lan import ShellyLanProvider

    return ShellyLanProvider(settings)


BUILTIN_PROVIDERS: dict[str, ProviderFactory] = {
    "govee_lan": _govee_lan,
    "ewelink_lan": _ewelink_lan,
    "yeelight_lan": _yeelight_lan,
    "wiz_lan": _wiz_lan,
    "shelly_lan": _shelly_lan,
}


class DeferredProvider(DeviceProvider):
    """Builds a provider at start and, if that fails, keeps trying in the background.

    Loading can fail for reasons outside the code, e.g. Windows Application Control blocking a
    compiled library at logon. One integration must not keep the whole backend from starting:
    until it loads, its devices are simply offline.
    """

    manufacturer = ""

    def __init__(self, key: str, factory: Callable[[], DeviceProvider], retry_s: float = RETRY_LOAD_S) -> None:
        self.key = key  # type: ignore[misc]
        self.manufacturer = key  # type: ignore[misc]
        self._factory = factory
        self._retry_s = retry_s
        self._inner: DeviceProvider | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def loaded(self) -> bool:
        return self._inner is not None

    async def _load(self, sink: ProviderEventSink) -> bool:
        try:
            inner = self._factory()
        except Exception as exc:
            log.warning("provider failed to load, will retry", extra={"ctx": {"provider": self.key, "error": repr(exc)}})
            return False
        if inner.key != self.key:
            raise ValueError(f"Provider {self.key!r} built a provider with key {inner.key!r}")
        self.manufacturer = inner.manufacturer  # type: ignore[misc]
        self._inner = inner
        await inner.start(sink)
        log.info("provider loaded", extra={"ctx": {"provider": self.key}})
        return True

    async def _retry(self, sink: ProviderEventSink) -> None:
        while True:
            await asyncio.sleep(self._retry_s)
            try:
                if await self._load(sink):
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("provider failed to start", extra={"ctx": {"provider": self.key}})
                return

    async def start(self, sink: ProviderEventSink) -> None:
        if not await self._load(sink):
            self._task = asyncio.create_task(self._retry(sink), name=f"load-{self.key}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self._inner is not None:
            await self._inner.stop()

    def _need(self, device: DeviceRecord) -> DeviceProvider:
        if self._inner is None:
            raise DeviceOfflineError(
                f"{device.name}: the {self.key} integration has not loaded yet",
                details={"device_id": device.id, "provider": self.key},
            )
        return self._inner

    async def discover(self, timeout_s: float) -> list[DiscoveredDevice]:
        return [] if self._inner is None else await self._inner.discover(timeout_s)

    async def get_state(self, device: DeviceRecord) -> DeviceState:
        return await self._need(device).get_state(device)

    async def execute(self, device: DeviceRecord, command: Command) -> DeviceState | None:
        return await self._need(device).execute(device, command)

    def segment_count(self, device: DeviceRecord) -> int:
        return 0 if self._inner is None else self._inner.segment_count(device)

    def supports_music(self, device: DeviceRecord) -> bool:
        return False if self._inner is None else self._inner.supports_music(device)

    async def music_styles(self, device: DeviceRecord) -> list[str]:
        return await self._need(device).music_styles(device)

    async def set_music(self, device: DeviceRecord, style: str, sensitivity: int) -> None:
        await self._need(device).set_music(device, style, sensitivity)

    async def set_segments(self, device: DeviceRecord, colors: list[RGB]) -> None:
        await self._need(device).set_segments(device, colors)


class ProviderRegistry:
    def __init__(self, providers: Iterable[DeviceProvider]) -> None:
        self._providers: dict[str, DeviceProvider] = {}
        for provider in providers:
            if provider.key in self._providers:
                raise ValueError(f"Duplicate provider key: {provider.key}")
            self._providers[provider.key] = provider

    @classmethod
    def from_settings(cls, settings: Settings) -> ProviderRegistry:
        unknown = [key for key in settings.enabled_providers if key not in BUILTIN_PROVIDERS]
        if unknown:
            raise ValueError(
                f"Unknown provider(s) in SHC_ENABLED_PROVIDERS: {unknown}. "
                f"Available: {sorted(BUILTIN_PROVIDERS)}"
            )
        return cls(
            DeferredProvider(key, lambda f=BUILTIN_PROVIDERS[key]: f(settings))
            for key in settings.enabled_providers
        )

    def get(self, key: str) -> DeviceProvider:
        try:
            return self._providers[key]
        except KeyError:
            raise ProviderUnavailableError(
                f"Provider {key!r} is not loaded", details={"provider": key}
            ) from None

    def has(self, key: str) -> bool:
        return key in self._providers

    def all(self) -> list[DeviceProvider]:
        return list(self._providers.values())

    def keys(self) -> list[str]:
        return list(self._providers)

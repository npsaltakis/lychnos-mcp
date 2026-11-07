"""Provider registry: which device integrations are loaded."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from app.core.config import Settings
from app.core.errors import ProviderUnavailableError
from app.providers.base import DeviceProvider

ProviderFactory = Callable[[Settings], DeviceProvider]

def _govee_lan(settings: Settings) -> DeviceProvider:
    from app.providers.govee_lan import GoveeLanProvider

    return GoveeLanProvider(settings)


def _ewelink_lan(settings: Settings) -> DeviceProvider:
    from app.providers.ewelink_lan import EwelinkLanProvider

    return EwelinkLanProvider(settings)


def _yeelight_lan(settings: Settings) -> DeviceProvider:
    from app.providers.yeelight_lan import YeelightLanProvider

    return YeelightLanProvider(settings)


BUILTIN_PROVIDERS: dict[str, ProviderFactory] = {
    "govee_lan": _govee_lan,
    "ewelink_lan": _ewelink_lan,
    "yeelight_lan": _yeelight_lan,
}


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
        return cls(BUILTIN_PROVIDERS[key](settings) for key in settings.enabled_providers)

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

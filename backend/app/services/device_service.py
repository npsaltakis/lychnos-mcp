"""Device service: the single place where devices are discovered, controlled and refreshed.

Both the REST/WebSocket API and the MCP server (through the API) go through this service,
so the dashboard and Claude always share the same state and the same validation.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Coroutine, Iterable
from typing import Any

from app.core.config import Settings
from app.core.errors import (
    AppError,
    CapabilityNotSupportedError,
    DeviceNotRegisteredError,
    DeviceTimeoutError,
    InvalidCommandError,
    NotFoundError,
    ProviderError,
)
from app.domain.models import (
    REQUIRED_CAPABILITY,
    BatchResult,
    Command,
    CommandOutcome,
    CommandResult,
    DeviceRecord,
    DeviceRuntime,
    DeviceState,
    DeviceView,
    DiscoveredDevice,
    ErrorInfo,
    PendingCommand,
    SetBrightness,
    SetColorTemperature,
    SkippedDevice,
    make_device_id,
    state_matches_command,
    utcnow,
)
from app.persistence.repositories import DeviceRepository
from app.providers.registry import ProviderRegistry
from app.services.events import EventBus, EventType
from app.services.state_store import StateStore

log = logging.getLogger(__name__)

# Delays (seconds) before each attempt of the first state read after startup.
INITIAL_REFRESH_DELAYS_S = (0, 3, 10, 30)


class DeviceService:
    def __init__(
        self,
        settings: Settings,
        repo: DeviceRepository,
        providers: ProviderRegistry,
        store: StateStore,
        bus: EventBus,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._providers = providers
        self._store = store
        self._bus = bus
        self._devices: dict[str, DeviceRecord] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._inflight_refresh: dict[str, asyncio.Task[DeviceRuntime]] = {}
        self._poll_task: asyncio.Task[None] | None = None
        self._last_client_activity = 0.0
        self._background: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        for device in await self._repo.list():
            self._devices[device.id] = device
        for provider in self._providers.all():
            try:
                await provider.start(self)
            except Exception as exc:
                # One broken integration must not take the whole backend (and the others) down.
                log.exception("provider failed to start", extra={"ctx": {"provider": provider.key}})
                self._bus.publish(
                    EventType.NOTIFICATION,
                    {"level": "warning", "message": f"{provider.manufacturer}: {exc}"},
                )
        self._poll_task = asyncio.create_task(self._poll_loop(), name="device-poll")
        # Initial state read for registered devices, in the background, with retries:
        # at Windows logon the Wi-Fi may connect a few seconds after the backend starts.
        for device in self._registered():
            if device.capabilities.supports_state_query and self._providers.has(device.provider):
                self._spawn(self._initial_refresh(device.id))
        log.info(
            "device service started",
            extra={"ctx": {"devices": len(self._devices), "providers": self._providers.keys()}},
        )

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        for task in [*self._inflight_refresh.values(), *self._background]:
            task.cancel()
        for provider in self._providers.all():
            try:
                await provider.stop()
            except Exception:
                log.exception("provider stop failed", extra={"ctx": {"provider": provider.key}})

    # ------------------------------------------------------------------ queries

    def note_client_activity(self) -> None:
        self._last_client_activity = time.monotonic()

    def list_devices(self, registered: bool | None = None) -> list[DeviceView]:
        devices = sorted(self._devices.values(), key=lambda d: d.name.lower())
        if registered is not None:
            devices = [d for d in devices if d.registered is registered]
        return [self._view(d) for d in devices]

    def get_device(self, device_id: str) -> DeviceRecord:
        device = self._devices.get(device_id)
        if device is None:
            raise NotFoundError(f"Unknown device {device_id!r}", details={"device_id": device_id})
        return device

    def get_view(self, device_id: str) -> DeviceView:
        return self._view(self.get_device(device_id))

    def provider_status(self) -> list[dict[str, str]]:
        return [{"key": p.key, "manufacturer": p.manufacturer} for p in self._providers.all()]

    # ------------------------------------------------------------------ registry management

    async def discover(self, timeout_s: float | None = None) -> list[DeviceView]:
        timeout = timeout_s or self._settings.discovery_timeout_s
        providers = self._providers.all()
        results = await asyncio.gather(
            *(p.discover(timeout) for p in providers), return_exceptions=True
        )
        found: list[DeviceView] = []
        for provider, result in zip(providers, results, strict=True):
            if isinstance(result, BaseException):
                log.warning(
                    "discovery failed",
                    extra={"ctx": {"provider": provider.key, "error": repr(result)}},
                )
                self._bus.publish(
                    EventType.NOTIFICATION,
                    {"level": "warning", "message": f"Discovery failed for {provider.manufacturer}"},
                )
                continue
            for discovered in result:
                found.append(self._view(await self._upsert_discovered(discovered)))
        return found

    async def update_device(
        self, device_id: str, *, name: str | None = None, registered: bool | None = None
    ) -> DeviceView:
        device = self.get_device(device_id)
        changes: dict[str, object] = {"updated_at": utcnow()}
        if name is not None:
            changes["name"] = name
        if registered is not None:
            changes["registered"] = registered
        updated = device.model_copy(update=changes)
        await self._repo.upsert(updated)
        self._devices[device_id] = updated
        self._bus.publish(EventType.DEVICE_UPDATED, {"device": updated.model_dump(mode="json")})
        if registered and not device.registered and updated.capabilities.supports_state_query:
            self._spawn(self._safe_refresh(device_id))
        return self._view(updated)

    async def delete_device(self, device_id: str) -> None:
        self.get_device(device_id)
        await self._repo.delete(device_id)
        self._devices.pop(device_id, None)
        self._store.remove(device_id)
        self._locks.pop(device_id, None)
        self._bus.publish(EventType.DEVICE_REMOVED, {"device_id": device_id})

    # ------------------------------------------------------------------ state

    async def refresh_state(self, device_id: str, *, force: bool = False) -> DeviceRuntime:
        """Read state from the device, reusing fresh cached state and in-flight reads."""
        device = self._require_registered(device_id)
        if not device.capabilities.supports_state_query:
            return self._store.get(device_id)
        if not force:
            age = self._store.age_seconds(device_id)
            if age is not None and age <= self._settings.state_max_age_s:
                return self._store.get(device_id)
        task = self._inflight_refresh.get(device_id)
        if task is None or task.done():
            task = asyncio.create_task(self._do_refresh(device))
            self._inflight_refresh[device_id] = task
        return await asyncio.shield(task)

    async def _do_refresh(self, device: DeviceRecord) -> DeviceRuntime:
        try:
            provider = self._providers.get(device.provider)
            try:
                state = await asyncio.wait_for(
                    provider.get_state(device), timeout=self._settings.command_timeout_s
                )
            except TimeoutError:
                raise DeviceTimeoutError(
                    f"{device.name} did not answer the status request",
                    details={"device_id": device.id},
                ) from None
            except AppError:
                raise
            except Exception as exc:
                log.exception("state read failed", extra={"ctx": {"device_id": device.id}})
                raise ProviderError(f"Reading {device.name} failed: {exc}") from exc
            self._store.apply_reported(device.id, state)
            return self._store.get(device.id)
        except AppError as error:
            self._store.record_failure(device.id, error)
            raise
        finally:
            self._inflight_refresh.pop(device.id, None)

    async def _initial_refresh(self, device_id: str) -> None:
        for delay in INITIAL_REFRESH_DELAYS_S:
            await asyncio.sleep(delay)
            if device_id not in self._devices:
                return
            try:
                await self.refresh_state(device_id, force=True)
                return
            except AppError as error:
                log.info(
                    "initial refresh failed, will retry",
                    extra={"ctx": {"device_id": device_id, "code": error.code, "next_in_s": delay}},
                )
            except Exception:
                log.exception("initial refresh crashed", extra={"ctx": {"device_id": device_id}})
                return

    async def _safe_refresh(self, device_id: str) -> None:
        try:
            await self.refresh_state(device_id, force=True)
        except AppError as error:
            log.info(
                "background refresh failed",
                extra={"ctx": {"device_id": device_id, "code": error.code}},
            )
        except Exception:
            log.exception("background refresh crashed", extra={"ctx": {"device_id": device_id}})

    # ------------------------------------------------------------------ commands

    def validate_command(self, device: DeviceRecord, command: Command) -> None:
        if not device.registered:
            raise DeviceNotRegisteredError(
                f"{device.name} is not registered for control", details={"device_id": device.id}
            )
        required = REQUIRED_CAPABILITY[type(command)]
        caps = device.capabilities
        if not caps.has(required):
            raise CapabilityNotSupportedError(
                f"{device.name} does not support {required.value}",
                details={
                    "device_id": device.id,
                    "capability": required.value,
                    "supported": sorted(c.value for c in caps.supported),
                },
            )
        if isinstance(command, SetBrightness) and command.brightness < caps.brightness_min:
            raise InvalidCommandError(
                f"Brightness for {device.name} must be between {caps.brightness_min} and 100",
                details={"device_id": device.id, "min": caps.brightness_min, "max": 100},
            )
        if isinstance(command, SetColorTemperature):
            rng = caps.color_temp_range
            assert rng is not None  # guaranteed by Capabilities validation
            if not rng.min_k <= command.kelvin <= rng.max_k:
                raise InvalidCommandError(
                    f"Colour temperature for {device.name} must be {rng.min_k}-{rng.max_k} K",
                    details={"device_id": device.id, "min_k": rng.min_k, "max_k": rng.max_k},
                )

    async def execute(self, device_id: str, command: Command) -> CommandResult:
        """Validate and send one command. Never reports success the device did not confirm."""
        device = self.get_device(device_id)
        self.validate_command(device, command)
        return await self._execute_validated(device, command)

    async def execute_sequence(self, device_id: str, commands: Iterable[Command]) -> list[CommandResult]:
        """Run several commands on one device in order, stopping at the first failure."""
        device = self.get_device(device_id)
        commands = list(commands)
        for command in commands:
            self.validate_command(device, command)
        results: list[CommandResult] = []
        for command in commands:
            result = await self._execute_validated(device, command)
            results.append(result)
            if result.outcome is CommandOutcome.FAILED:
                break
        return results

    async def execute_on_all(self, command: Command) -> BatchResult:
        """Send a command to every registered device that supports it."""
        targets: list[DeviceRecord] = []
        skipped: list[SkippedDevice] = []
        for device in self._registered():
            try:
                self.validate_command(device, command)
            except AppError as error:
                skipped.append(SkippedDevice(device_id=device.id, reason=error.message))
                continue
            targets.append(device)
        results = await asyncio.gather(*(self._execute_validated(d, command) for d in targets))
        return BatchResult.build(list(results), skipped)

    async def _execute_validated(self, device: DeviceRecord, command: Command) -> CommandResult:
        lock = self._locks.setdefault(device.id, asyncio.Lock())
        async with lock:
            command_id = uuid.uuid4().hex[:12]
            started = time.perf_counter()
            self._store.set_pending(device.id, PendingCommand(command_id=command_id, command=command))
            error: AppError | None = None
            observed: DeviceState | None = None
            try:
                provider = self._providers.get(device.provider)
                observed = await asyncio.wait_for(
                    provider.execute(device, command), timeout=self._settings.command_timeout_s
                )
            except TimeoutError:
                error = DeviceTimeoutError(
                    f"{device.name} did not respond in time", details={"device_id": device.id}
                )
            except AppError as exc:
                error = exc
            except Exception as exc:
                log.exception("command crashed", extra={"ctx": {"device_id": device.id}})
                error = ProviderError(f"Command to {device.name} failed: {exc}")
            finally:
                self._store.clear_pending(device.id, command_id)

            if error is not None:
                self._store.record_failure(device.id, error)
                outcome = CommandOutcome.FAILED
            elif observed is None:
                self._store.mark_reachable(device.id)
                outcome = CommandOutcome.UNCONFIRMED
            else:
                self._store.apply_reported(device.id, observed)
                outcome = (
                    CommandOutcome.CONFIRMED
                    if state_matches_command(command, observed)
                    else CommandOutcome.MISMATCH
                )

            runtime = self._store.get(device.id)
            result = CommandResult(
                command_id=command_id,
                device_id=device.id,
                command=command,
                outcome=outcome,
                state=runtime.state,
                availability=runtime.availability,
                error=ErrorInfo(code=error.code, message=error.message) if error else None,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        log.info(
            "command executed",
            extra={
                "ctx": {
                    "device_id": device.id,
                    "command": command.type,
                    "outcome": outcome.value,
                    "duration_ms": result.duration_ms,
                    "error": error.code if error else None,
                }
            },
        )
        self._bus.publish(EventType.COMMAND_RESULT, result.model_dump(mode="json"))
        return result

    # ------------------------------------------------------------------ provider events (sink)

    async def state_reported(
        self, provider: str, native_id: str, state: DeviceState, *, partial: bool = False
    ) -> None:
        device_id = make_device_id(provider, native_id)
        if device_id in self._devices:
            self._store.apply_reported(device_id, state, partial=partial)

    async def address_changed(self, provider: str, native_id: str, address: str) -> None:
        device_id = make_device_id(provider, native_id)
        device = self._devices.get(device_id)
        if device is None or device.address == address:
            return
        updated = device.model_copy(update={"address": address, "updated_at": utcnow()})
        await self._repo.upsert(updated)
        self._devices[device_id] = updated
        self._bus.publish(EventType.DEVICE_UPDATED, {"device": updated.model_dump(mode="json")})

    # ------------------------------------------------------------------ internals

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _registered(self) -> list[DeviceRecord]:
        return [d for d in self._devices.values() if d.registered]

    def _require_registered(self, device_id: str) -> DeviceRecord:
        device = self.get_device(device_id)
        if not device.registered:
            raise DeviceNotRegisteredError(
                f"{device.name} is not registered", details={"device_id": device_id}
            )
        return device

    def _view(self, device: DeviceRecord) -> DeviceView:
        return DeviceView(device=device, runtime=self._store.get(device.id))

    async def _upsert_discovered(self, discovered: DiscoveredDevice) -> DeviceRecord:
        device_id = make_device_id(discovered.provider, discovered.native_id)
        existing = self._devices.get(device_id)
        if existing is None:
            record = DeviceRecord(id=device_id, **discovered.model_dump())
        else:
            # Keep user choices (name, registration); refresh what the network reports.
            record = existing.model_copy(
                update={
                    "model": discovered.model or existing.model,
                    "address": discovered.address or existing.address,
                    "capabilities": discovered.capabilities,
                    "metadata": {**existing.metadata, **discovered.metadata},
                    "updated_at": utcnow(),
                }
            )
        await self._repo.upsert(record)
        self._devices[device_id] = record
        self._bus.publish(EventType.DEVICE_UPDATED, {"device": record.model_dump(mode="json")})
        return record

    async def _poll_loop(self) -> None:
        """Poll devices without push support, only while someone is watching."""
        interval = self._settings.poll_interval_s
        while True:
            await asyncio.sleep(interval)
            watching = self._bus.subscriber_count > 0 or (
                time.monotonic() - self._last_client_activity < interval * 2
            )
            if not watching:
                continue
            targets = [
                d.id
                for d in self._registered()
                if d.capabilities.supports_state_query
                and not d.capabilities.supports_push
                and self._providers.has(d.provider)
            ]
            if targets:
                await asyncio.gather(*(self._safe_refresh(t) for t in targets))

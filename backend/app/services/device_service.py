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
from typing import Any, Callable

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
    RGB,
    BatchResult,
    Command,
    CommandOutcome,
    CommandResult,
    DeviceRecord,
    DeviceRuntime,
    Availability,
    DeviceState,
    DeviceView,
    DiscoveredDevice,
    LightMode,
    ErrorInfo,
    PendingCommand,
    SetBrightness,
    SetColor,
    SetColorTemperature,
    SetPower,
    SkippedDevice,
    make_device_id,
    state_matches_command,
    utcnow,
)
from app.persistence.repositories import ActivityRepository, DeviceRepository
from app.providers.registry import ProviderRegistry
from app.services.events import EventBus, EventType
from app.services.state_store import StateStore

log = logging.getLogger(__name__)

# Delays (seconds) before each attempt of the first state read after startup.
INITIAL_REFRESH_DELAYS_S = (0, 3, 10, 30)

UNSET: object = object()  # "argument not given" marker (None is a meaningful value)


# A state change within this many seconds of our own matching command is attributed to it.
COMMAND_ATTRIBUTION_S = 20.0
QUIET_SOURCES = {"effect"}  # many small steps: not written to the change log


def explained_fields(command: Command) -> set[str]:
    """State fields a command is expected to change (colour/brightness may also switch a light on)."""
    match command:
        case SetPower():
            return {"power"}
        case SetBrightness():
            return {"brightness", "power"}
        case SetColor():
            return {"color", "power"}
        case SetColorTemperature():
            return {"color_temp", "power"}
    return set()


def describe_changes(old: DeviceState, new: DeviceState) -> list[tuple[str, str]]:
    """Human-readable changes between two confirmed states; nothing for values seen the first time."""
    changes: list[tuple[str, str]] = []
    if old.power is not None and new.power is not None and old.power != new.power:
        changes.append(("power", "ON" if new.power else "OFF"))
    if old.brightness is not None and new.brightness is not None and abs(old.brightness - new.brightness) > 1:
        changes.append(("brightness", f"brightness {new.brightness}%"))
    if old.mode is not None and new.mode is LightMode.COLOR and new.color is not None:
        if old.mode is not LightMode.COLOR or old.color is None or not old.color.close_to(new.color):
            changes.append(("color", f"color {new.color.to_hex()}"))
    if old.mode is not None and new.mode is LightMode.WHITE and new.color_temp_k is not None:
        if old.mode is not LightMode.WHITE or old.color_temp_k is None or abs(old.color_temp_k - new.color_temp_k) > 100:
            changes.append(("color_temp", f"white {new.color_temp_k}K"))
    return changes


def describe_command(command: Command) -> str:
    match command:
        case SetPower(on=on):
            return "ON" if on else "OFF"
        case SetBrightness(brightness=b):
            return f"brightness {b}%"
        case SetColor(color=c):
            return f"color {c.to_hex()}"
        case SetColorTemperature(kelvin=k):
            return f"white {k}K"
    return command.type


class DeviceService:
    def __init__(
        self,
        settings: Settings,
        repo: DeviceRepository,
        providers: ProviderRegistry,
        store: StateStore,
        bus: EventBus,
        activity: ActivityRepository | None = None,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._activity = activity
        self._providers = providers
        self._store = store
        self._bus = bus
        self._devices: dict[str, DeviceRecord] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._inflight_refresh: dict[str, asyncio.Task[DeviceRuntime]] = {}
        self._poll_task: asyncio.Task[None] | None = None
        self._last_client_activity = 0.0
        self._background: set[asyncio.Task[None]] = set()
        # Commands sent recently per device, to tell our own changes from external ones
        # (Google Home, the vendor app, a wall switch).
        self._recent_commands: dict[str, list[tuple[Command, float]]] = {}
        store.listeners.append(self._on_state_change)
        # Called with (device_id, source) before every command (effects use it to stop themselves).
        self.command_listeners: list[Callable[[str, str], None]] = []
        # Lights in music mode; any other command takes them out of it.
        self._music_on: set[str] = set()
        self.command_listeners.append(lambda device_id, _source: self._music_on.discard(device_id))

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
        self,
        device_id: str,
        *,
        name: str | None = None,
        registered: bool | None = None,
        room_id: str | None | object = UNSET,
    ) -> DeviceView:
        """Change name, registration or room. ``room_id=None`` removes the device from its room."""
        device = self.get_device(device_id)
        changes: dict[str, object] = {"updated_at": utcnow()}
        if name is not None:
            changes["name"] = name
        if registered is not None:
            changes["registered"] = registered
        if room_id is not UNSET:
            changes["room_id"] = room_id
        updated = device.model_copy(update=changes)
        await self._repo.upsert(updated)
        self._devices[device_id] = updated
        self._bus.publish(EventType.DEVICE_UPDATED, {"device": updated.model_dump(mode="json")})
        if registered and not device.registered and updated.capabilities.supports_state_query:
            self._spawn(self._safe_refresh(device_id))
        return self._view(updated)

    def room_removed(self, room_id: str) -> None:
        """Forget a deleted room in the cache (the database row was already cleared)."""
        for device_id, device in list(self._devices.items()):
            if device.room_id == room_id:
                updated = device.model_copy(update={"room_id": None})
                self._devices[device_id] = updated
                self._bus.publish(EventType.DEVICE_UPDATED, {"device": updated.model_dump(mode="json")})

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

    async def execute(self, device_id: str, command: Command, *, source: str = "api") -> CommandResult:
        """Validate and send one command. Never reports success the device did not confirm."""
        device = self.get_device(device_id)
        self.validate_command(device, command)
        return await self._execute_validated(device, command, source)

    async def execute_sequence(
        self, device_id: str, commands: Iterable[Command], *, source: str = "api"
    ) -> list[CommandResult]:
        """Run several commands on one device in order, stopping at the first failure."""
        device = self.get_device(device_id)
        commands = list(commands)
        for command in commands:
            self.validate_command(device, command)
        results: list[CommandResult] = []
        for command in commands:
            result = await self._execute_validated(device, command, source)
            results.append(result)
            if result.outcome is CommandOutcome.FAILED:
                break
        return results

    async def execute_on_all(self, command: Command, *, source: str = "api") -> BatchResult:
        """Send a command to every registered device that supports it."""
        return await self.execute_on_group(command, room_id=None, source=source)

    async def execute_on_group(
        self, command: Command, *, room_id: str | None, source: str = "api"
    ) -> BatchResult:
        """Send a command to every registered device (of one room, if given) that supports it."""
        targets: list[DeviceRecord] = []
        skipped: list[SkippedDevice] = []
        for device in self._registered():
            if room_id is not None and device.room_id != room_id:
                continue
            try:
                self.validate_command(device, command)
            except AppError as error:
                skipped.append(SkippedDevice(device_id=device.id, reason=error.message))
                continue
            targets.append(device)
        results = await asyncio.gather(*(self._execute_validated(d, command, source) for d in targets))
        return BatchResult.build(list(results), skipped)

    def music_on(self, device_id: str) -> bool:
        return device_id in self._music_on

    async def stop_music(self, device_id: str, *, source: str = "api") -> DeviceRuntime:
        """Leave music mode by re-sending the light's last colour (any LAN command ends it)."""
        device = self._require_registered(device_id)
        state = self._store.get(device.id).state
        command: Command
        if state.mode is LightMode.WHITE and state.color_temp_k:
            command = SetColorTemperature(kelvin=state.color_temp_k)
        elif state.color is not None:
            command = SetColor(color=state.color)
        else:
            command = SetPower(on=True)
        await self.execute(device_id, command, source=source)
        self._music_on.discard(device.id)
        return self._store.get(device.id)

    async def music_styles(self, device_id: str) -> list[str]:
        device = self._require_registered(device_id)
        provider = self._providers.get(device.provider)
        if not provider.supports_music(device):
            return []
        return await provider.music_styles(device)

    async def set_music(self, device_id: str, style: str, sensitivity: int, *, source: str = "api") -> DeviceRuntime:
        """Let the light follow sound with its own microphone (until it gets another command)."""
        device = self._require_registered(device_id)
        provider = self._providers.get(device.provider)
        if not provider.supports_music(device):
            raise InvalidCommandError(f"{device.name} has no music mode")
        for listener in self.command_listeners:
            listener(device.id, source)
        if self._store.get(device.id).state.power is not True:
            await self._execute_validated(device, SetPower(on=True), source)
        await provider.set_music(device, style, sensitivity)
        self._music_on.add(device.id)
        if self._activity is not None:
            await self._activity.add(
                ts=utcnow(), source=source, device_id=device.id, device_name=device.name,
                command=f"music {style}", outcome="unconfirmed",
            )
            self._bus.publish(EventType.ACTIVITY_ADDED)
        return self._store.get(device.id)

    def segment_count(self, device_id: str) -> int:
        device = self.get_device(device_id)
        return self._providers.get(device.provider).segment_count(device)

    async def set_segments(self, device_id: str, colors: list[RGB], *, source: str = "api") -> DeviceRuntime:
        """Colour each segment of an RGBIC light. Sent once; the light cannot confirm it."""
        device = self._require_registered(device_id)
        count = self.segment_count(device_id)
        if count == 0:
            raise InvalidCommandError(f"{device.name} has no colour segments")
        if len(colors) != count:
            raise InvalidCommandError(f"{device.name} has {count} segments; got {len(colors)} colours")
        for listener in self.command_listeners:
            listener(device.id, source)
        provider = self._providers.get(device.provider)
        await provider.set_segments(device, colors)
        await asyncio.sleep(0.5)
        runtime = await self.refresh_state(device_id, force=True)
        if self._activity is not None:
            await self._activity.add(
                ts=utcnow(), source=source, device_id=device.id, device_name=device.name,
                command="segments " + " ".join(c.to_hex() for c in colors), outcome="unconfirmed",
            )
            self._bus.publish(EventType.ACTIVITY_ADDED)
        return runtime

    def devices_in(self, room_id: str | None) -> list[DeviceRecord]:
        """Registered devices of a room (or all registered devices when room_id is None)."""
        return [d for d in self._registered() if room_id is None or d.room_id == room_id]

    async def _execute_validated(self, device: DeviceRecord, command: Command, source: str = "api") -> CommandResult:
        for listener in self.command_listeners:
            try:
                listener(device.id, source)
            except Exception:
                log.exception("command listener failed")
        lock = self._locks.setdefault(device.id, asyncio.Lock())
        async with lock:
            command_id = uuid.uuid4().hex[:12]
            started = time.perf_counter()
            now = time.monotonic()
            recent = [c for c in self._recent_commands.get(device.id, []) if now - c[1] < COMMAND_ATTRIBUTION_S]
            self._recent_commands[device.id] = [*recent, (command, now)]
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
                    "source": source,
                }
            },
        )
        self._bus.publish(EventType.COMMAND_RESULT, result.model_dump(mode="json"))
        if self._activity is not None and source not in QUIET_SOURCES:
            try:
                await self._activity.add(
                    ts=utcnow(),
                    source=source,
                    device_id=device.id,
                    device_name=device.name,
                    command=describe_command(command),
                    outcome=outcome.value,
                    detail=error.message if error else None,
                )
                self._bus.publish(EventType.ACTIVITY_ADDED)
            except Exception:
                log.exception("activity log write failed")
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

    # ------------------------------------------------------------------ change log

    def _on_state_change(
        self,
        device_id: str,
        old: DeviceState,
        new: DeviceState,
        old_av: Availability,
        new_av: Availability,
    ) -> None:
        """Record changes nobody asked Lychnos for, and availability changes, in the history."""
        device = self._devices.get(device_id)
        if device is None or not device.registered or self._activity is None:
            return
        entries: list[tuple[str, str, str]] = []  # (source, text, outcome)
        if old_av != new_av and old_av is not Availability.UNKNOWN:
            if new_av is Availability.OFFLINE:
                entries.append(("system", "offline", "offline"))
            elif new_av is Availability.ONLINE and old_av is Availability.OFFLINE:
                entries.append(("system", "online", "online"))
        now = time.monotonic()
        recent = [c for c, ts in self._recent_commands.get(device_id, []) if now - ts < COMMAND_ATTRIBUTION_S]
        for field, text in describe_changes(old, new):
            if not any(field in explained_fields(c) for c in recent):
                entries.append(("external", text, "observed"))
        for source, text, outcome in entries:
            self._spawn(self._log_activity(device, source, text, outcome))

    async def _log_activity(self, device: DeviceRecord, source: str, text: str, outcome: str) -> None:
        assert self._activity is not None
        try:
            await self._activity.add(
                ts=utcnow(), source=source, device_id=device.id, device_name=device.name,
                command=text, outcome=outcome,
            )
            self._bus.publish(EventType.ACTIVITY_ADDED)
        except Exception:
            log.exception("activity log write failed")

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
        """Poll devices without push support.

        By default always (so changes made from other apps are logged and alerts work);
        with SHC_POLL_ALWAYS=false only while a dashboard/client is watching.
        """
        interval = self._settings.poll_interval_s
        while True:
            await asyncio.sleep(interval)
            watching = self._settings.poll_always or self._bus.subscriber_count > 0 or (
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

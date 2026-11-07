"""In-memory live state per device, separating confirmed state from pending commands.

Rules:
- ``state`` only ever contains values reported by the device itself.
- A command in flight is kept in ``pending``; it never overwrites ``state``.
- Every change is published on the event bus.
"""

from __future__ import annotations

from app.core.errors import AppError
from app.domain.models import (
    Availability,
    DeviceRuntime,
    DeviceState,
    ErrorInfo,
    PendingCommand,
    utcnow,
)
from app.services.events import EventBus, EventType


class StateStore:
    def __init__(self, bus: EventBus, offline_after_failures: int) -> None:
        self._bus = bus
        self._offline_after = offline_after_failures
        self._runtimes: dict[str, DeviceRuntime] = {}

    # -- reads
    def get(self, device_id: str) -> DeviceRuntime:
        """Return a copy of the runtime (callers must not mutate internal state)."""
        return self._runtime(device_id).model_copy(deep=True)

    def age_seconds(self, device_id: str) -> float | None:
        confirmed_at = self._runtime(device_id).state_confirmed_at
        return None if confirmed_at is None else (utcnow() - confirmed_at).total_seconds()

    # -- writes
    def set_pending(self, device_id: str, pending: PendingCommand) -> None:
        runtime = self._runtime(device_id)
        runtime.pending = pending
        self._emit(device_id, runtime)

    def clear_pending(self, device_id: str, command_id: str) -> None:
        runtime = self._runtime(device_id)
        if runtime.pending is not None and runtime.pending.command_id == command_id:
            runtime.pending = None
            self._emit(device_id, runtime)

    def apply_reported(self, device_id: str, reported: DeviceState, *, partial: bool = False) -> None:
        """Store a state reported by the device and mark it online.

        Full snapshots (status reads) replace the state; partial reports (push updates
        that only carry changed fields) are merged into it.
        """
        runtime = self._runtime(device_id)
        now = utcnow()
        runtime.state = runtime.state.merged_with(reported) if partial else reported
        runtime.state_confirmed_at = now
        runtime.last_seen_at = now
        runtime.availability = Availability.ONLINE
        runtime.consecutive_failures = 0
        runtime.last_error = None
        self._emit(device_id, runtime)

    def mark_reachable(self, device_id: str) -> None:
        """The device accepted a command but reported no state."""
        runtime = self._runtime(device_id)
        runtime.last_seen_at = utcnow()
        runtime.availability = Availability.ONLINE
        runtime.consecutive_failures = 0
        runtime.last_error = None
        self._emit(device_id, runtime)

    def record_failure(self, device_id: str, error: AppError) -> None:
        runtime = self._runtime(device_id)
        runtime.consecutive_failures += 1
        runtime.last_error = ErrorInfo(code=error.code, message=error.message)
        if runtime.consecutive_failures >= self._offline_after:
            runtime.availability = Availability.OFFLINE
        self._emit(device_id, runtime)

    def remove(self, device_id: str) -> None:
        self._runtimes.pop(device_id, None)

    # -- internals
    def _runtime(self, device_id: str) -> DeviceRuntime:
        runtime = self._runtimes.get(device_id)
        if runtime is None:
            runtime = self._runtimes[device_id] = DeviceRuntime()
        return runtime

    def _emit(self, device_id: str, runtime: DeviceRuntime) -> None:
        self._bus.publish(
            EventType.DEVICE_RUNTIME,
            {"device_id": device_id, "runtime": runtime.model_dump(mode="json")},
        )

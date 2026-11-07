"""In-process publish/subscribe used to push live updates to WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import utcnow

log = logging.getLogger(__name__)


class EventType(StrEnum):
    DEVICE_RUNTIME = "device_runtime"  # live state / availability / pending changed
    DEVICE_UPDATED = "device_updated"  # record changed (name, registration, address...)
    DEVICE_REMOVED = "device_removed"
    COMMAND_RESULT = "command_result"
    SCENES_CHANGED = "scenes_changed"
    ROOMS_CHANGED = "rooms_changed"
    AUTOMATIONS_CHANGED = "automations_changed"
    ACTIVITY_ADDED = "activity_added"
    AUTOMATION_RAN = "automation_ran"
    NOTIFICATION = "notification"
    RESYNC_REQUIRED = "resync_required"  # subscriber fell behind; reload full state


class Event(BaseModel):
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=utcnow)


class EventBus:
    def __init__(self, max_queue: int = 200) -> None:
        self._max_queue = max_queue
        self._subscribers: set[asyncio.Queue[Event]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(self._max_queue)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, event_type: EventType, data: dict[str, Any] | None = None) -> None:
        event = Event(type=event_type, data=data or {})
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop its backlog and tell it to reload everything.
                log.warning("event subscriber overflow; requesting resync")
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(Event(type=EventType.RESYNC_REQUIRED))

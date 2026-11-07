"""WebSocket endpoint for live updates.

Programs send the API token as a subprotocol: ``["shc.v1", "bearer." + token]``.
The dashboard uses its session cookie instead (``["shc.v1"]``). The server answers "shc.v1".
On connect the client receives a full ``snapshot``; afterwards incremental events.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.api.deps import SESSION_COOKIE
from app.core.security import tokens_equal
from app.services.auth_service import AuthService
from app.services.device_service import DeviceService
from app.services.events import EventBus

log = logging.getLogger(__name__)
router = APIRouter()

PROTOCOL = "shc.v1"
TOKEN_PREFIX = "bearer."


def _token_from_subprotocols(ws: WebSocket) -> str | None:
    for proto in ws.scope.get("subprotocols", []):
        if proto.startswith(TOKEN_PREFIX):
            return proto[len(TOKEN_PREFIX) :]
    return None


async def _authorized(ws: WebSocket) -> bool:
    token = _token_from_subprotocols(ws)
    if token is not None:
        return tokens_equal(token, ws.app.state.api_token)
    # Browser dashboard: session cookie + our own origin (blocks cross-site WebSocket hijacking).
    origin = ws.headers.get("origin")
    if origin is None or origin not in ws.app.state.settings.dashboard_origins:
        return False
    auth: AuthService = ws.app.state.auth_service
    return await auth.validate(ws.cookies.get(SESSION_COOKIE))


@router.websocket("/api/ws")
async def live_updates(ws: WebSocket) -> None:
    if not await _authorized(ws):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    bus: EventBus = ws.app.state.event_bus
    devices: DeviceService = ws.app.state.device_service
    await ws.accept(subprotocol=PROTOCOL)

    with bus.subscribe() as queue:
        await ws.send_json(
            {
                "type": "snapshot",
                "data": {"devices": [v.model_dump(mode="json") for v in devices.list_devices()]},
            }
        )

        async def pump() -> None:
            while True:
                event = await queue.get()
                await ws.send_json(event.model_dump(mode="json"))

        async def drain() -> None:
            # Client messages are ignored; receiving detects disconnects.
            while True:
                await ws.receive_text()

        tasks = [asyncio.create_task(pump()), asyncio.create_task(drain())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, WebSocketDisconnect):
                    log.warning("websocket closed with error", extra={"ctx": {"error": repr(exc)}})
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

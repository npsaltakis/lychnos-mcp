"""Yeelight LAN provider (Xiaomi / Yeelight lights, local TCP, no cloud, no keys).

One persistent TCP connection per light is used both for commands and for the light's
``props`` push notifications, so changes made from other apps show up immediately.
Commands are confirmed by reading the properties back after the light answers "ok".
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import socket
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import Settings
from app.core.errors import DeviceOfflineError, DeviceTimeoutError, ProviderError
from app.domain.models import Command, DeviceRecord, DeviceState, DiscoveredDevice
from app.providers.base import DeviceProvider, ProviderEventSink
from app.providers.yeelight_lan import protocol

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 2.0
REQUEST_TIMEOUT_S = 2.0

PropsCallback = Callable[[dict[str, Any]], Awaitable[None]]


class _Connection:
    """One TCP connection to a light: request/response by id + push notifications."""

    def __init__(
        self, host: str, port: int, on_props: PropsCallback, on_lost: Callable[[], None] | None = None
    ) -> None:
        self.host, self.port = host, port
        self._on_props = on_props
        self._on_lost = on_lost
        self._closing = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def ensure(self) -> None:
        if self.connected:
            return
        self._closing = False
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT_S
            )
        except TimeoutError:
            raise DeviceTimeoutError(f"Yeelight at {self.host} did not accept the connection") from None
        except OSError as exc:
            raise DeviceOfflineError(
                f"Cannot connect to Yeelight at {self.host}:{self.port}. Is 'LAN Control' enabled?"
            ) from exc
        self._task = asyncio.create_task(self._read_loop())

    async def request(self, method: str, params: list[Any]) -> list[Any]:
        async with self._lock:  # the light handles requests one at a time
            await self.ensure()
            assert self._writer is not None
            request_id = next(self._ids)
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._pending[request_id] = future
            try:
                self._writer.write(protocol.encode_request(request_id, method, params))
                await self._writer.drain()
                answer = await asyncio.wait_for(future, REQUEST_TIMEOUT_S)
            except TimeoutError:
                await self.close()
                raise DeviceTimeoutError(f"Yeelight at {self.host} did not answer {method}") from None
            except OSError as exc:
                await self.close()
                raise DeviceOfflineError(f"Connection to Yeelight at {self.host} was lost") from exc
            finally:
                self._pending.pop(request_id, None)
        if "error" in answer:
            error = answer["error"] or {}
            raise ProviderError(
                f"Yeelight rejected {method}: {error.get('message', 'error')}",
                details={"method": method, "device_error": error},
            )
        result = answer.get("result")
        return result if isinstance(result, list) else []

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                message = protocol.decode_line(line)
                if message is None:
                    continue
                if message.get("method") == "props" and isinstance(message.get("params"), dict):
                    await self._on_props(message["params"])
                elif (future := self._pending.get(message.get("id", -1))) and not future.done():
                    future.set_result(message)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(OSError("connection closed"))
            if self._writer is not None:
                self._writer.close()
            self._writer = None
            if not self._closing and self._on_lost is not None:
                self._on_lost()

    async def close(self) -> None:
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._writer is not None:
            self._writer.close()
            self._writer = None


class _SearchProtocol(asyncio.DatagramProtocol):
    def __init__(self, results: dict[str, dict[str, str]]) -> None:
        self.results = results

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        headers = protocol.parse_search_response(data)
        if headers is not None:
            self.results[headers["id"]] = headers


class YeelightLanProvider(DeviceProvider):
    key = "yeelight_lan"
    manufacturer = "Yeelight"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connections: dict[str, _Connection] = {}
        self._state: dict[str, DeviceState] = {}
        self._sink: ProviderEventSink | None = None
        self._stopping = False
        self._reconnecting: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self, sink: ProviderEventSink) -> None:
        self._sink = sink

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        for connection in self._connections.values():
            await connection.close()
        self._connections.clear()

    # ------------------------------------------------------------------ discovery

    async def discover(self, timeout_s: float) -> list[DiscoveredDevice]:
        results: dict[str, dict[str, str]] = {}
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.bind((self._settings.lan_interface_ip or "0.0.0.0", 0))
        if self._settings.lan_interface_ip:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self._settings.lan_interface_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setblocking(False)
        transport, _ = await loop.create_datagram_endpoint(lambda: _SearchProtocol(results), sock=sock)
        try:
            for _ in range(3):
                transport.sendto(protocol.SEARCH_REQUEST, (protocol.MULTICAST_GROUP, protocol.SSDP_PORT))
                await asyncio.sleep(min(0.5, timeout_s / 3))
            await asyncio.sleep(max(0.0, timeout_s - 1.5))
        finally:
            transport.close()

        devices = []
        for device_id, headers in results.items():
            host, port = protocol.location_host_port(headers["location"])
            model = headers.get("model", "")
            devices.append(
                DiscoveredDevice(
                    provider=self.key,
                    native_id=device_id,
                    name=protocol.friendly_name(headers),
                    manufacturer=self.manufacturer,
                    model=model or None,
                    address=host,
                    capabilities=protocol.capabilities_for(model, headers.get("support", "")),
                    metadata={"port": port, "firmware": headers.get("fw_ver"), "support": headers.get("support", "")},
                )
            )
        log.info("yeelight discovery finished", extra={"ctx": {"found": len(devices)}})
        return devices

    # ------------------------------------------------------------------ state & control

    async def get_state(self, device: DeviceRecord) -> DeviceState:
        connection = self._connection(device)
        values = await connection.request("get_prop", protocol.STATE_PROPS)
        props = dict(zip(protocol.STATE_PROPS, values, strict=False))
        state, _ = protocol.state_from_props(props)
        self._state[device.native_id] = state
        return state

    async def execute(self, device: DeviceRecord, command: Command) -> DeviceState | None:
        connection = self._connection(device)
        current = self._state.get(device.native_id) or await self.get_state(device)
        for method, params in protocol.commands_for(command, current):
            await connection.request(method, params)
        # The light answered "ok"; read the real state back as confirmation.
        return await self.get_state(device)

    # ------------------------------------------------------------------ helpers

    def _connection(self, device: DeviceRecord) -> _Connection:
        if not device.address:
            raise DeviceOfflineError(f"{device.name} has no known IP address; run discovery")
        port = int(device.metadata.get("port") or protocol.DEFAULT_PORT)
        connection = self._connections.get(device.native_id)
        if connection is None or (connection.host, connection.port) != (device.address, port):
            if connection is not None:  # address changed: drop the old connection
                task = asyncio.get_running_loop().create_task(connection.close())
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            native_id = device.native_id

            async def on_props(params: dict[str, Any]) -> None:
                await self._on_props(native_id, params)

            connection = _Connection(
                device.address, port, on_props, on_lost=lambda: self._schedule_reconnect(device)
            )
            self._connections[device.native_id] = connection
        return connection

    def _schedule_reconnect(self, device: DeviceRecord) -> None:
        """Keep the push channel alive: reconnect with backoff and resync the state."""
        if self._stopping or device.native_id in self._reconnecting:
            return
        self._reconnecting.add(device.native_id)

        async def run() -> None:
            delay = 2.0
            try:
                while not self._stopping and device.native_id in self._connections:
                    await asyncio.sleep(delay)
                    try:
                        state = await self.get_state(device)
                    except Exception:
                        delay = min(60.0, delay * 2)
                        continue
                    if self._sink is not None:
                        await self._sink.state_reported(self.key, device.native_id, state)
                    log.info("yeelight reconnected", extra={"ctx": {"device": device.native_id}})
                    return
            finally:
                self._reconnecting.discard(device.native_id)

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _on_props(self, native_id: str, params: dict[str, Any]) -> None:
        reported, full = protocol.state_from_props(params)
        current = self._state.get(native_id, DeviceState())
        self._state[native_id] = reported if full else current.merged_with(reported)
        if self._sink is not None:
            await self._sink.state_reported(self.key, native_id, reported, partial=not full)

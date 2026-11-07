"""Govee LAN API provider (local UDP, no cloud).

One UDP socket bound to :4002 receives both scan replies and devStatus replies.
Replies are matched to requests by the sender's IP address.

Because Govee does not acknowledge control commands, ``execute`` confirms them by reading
``devStatus`` back until the device reports the requested value or the deadline passes.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

from app.core.config import Settings
from app.core.errors import DeviceOfflineError, DeviceTimeoutError, ProviderError
from app.domain.models import (
    Command,
    DeviceRecord,
    DeviceState,
    DiscoveredDevice,
    state_matches_command,
)
from app.providers.base import DeviceProvider, ProviderEventSink
from app.providers.govee_lan import protocol

log = logging.getLogger(__name__)

STATUS_TIMEOUT_S = 0.7
STATUS_ATTEMPTS = 2
CONFIRM_DELAYS_S = (0.15, 0.3, 0.5)  # read-back schedule after a command


def detect_local_ip() -> str:
    """IPv4 address of the interface that routes to the LAN (no packets are sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed anywhere
        return probe.getsockname()[0]


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, owner: GoveeLanProvider) -> None:
        self._owner = owner

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._owner._on_datagram(data, addr[0])

    def error_received(self, exc: Exception) -> None:
        log.debug("govee socket error", extra={"ctx": {"error": repr(exc)}})


class GoveeLanProvider(DeviceProvider):
    key = "govee_lan"
    manufacturer = "Govee"

    def __init__(self, settings: Settings, local_ip: str | None = None) -> None:
        self._settings = settings
        self._local_ip = local_ip or settings.lan_interface_ip
        self._transport: asyncio.DatagramTransport | None = None
        self._status_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._scan_results: dict[str, dict[str, Any]] | None = None
        self._ip_locks: dict[str, asyncio.Lock] = {}
        self._scan_lock = asyncio.Lock()
        self._sink: ProviderEventSink | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self, sink: ProviderEventSink) -> None:
        self._sink = sink
        local_ip = self._local_ip
        if local_ip is None:
            try:
                local_ip = detect_local_ip()
            except OSError:
                # e.g. started at Windows logon before Wi-Fi is connected: use the OS default
                # multicast interface; unicast status/commands work as soon as the network is up.
                log.warning("govee: no network yet; using the default multicast interface")
        self._local_ip = local_ip
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.bind(("0.0.0.0", protocol.LISTEN_PORT))
        except OSError as exc:
            sock.close()
            raise ProviderError(
                f"Govee: UDP port {protocol.LISTEN_PORT} is in use by another program "
                "(e.g. another Govee integration). Close it and restart.",
                details={"port": protocol.LISTEN_PORT},
            ) from exc
        if local_ip:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setblocking(False)
        transport, _ = await loop.create_datagram_endpoint(lambda: _Protocol(self), sock=sock)
        self._transport = transport  # type: ignore[assignment]
        log.info("govee lan listening", extra={"ctx": {"port": protocol.LISTEN_PORT, "iface": local_ip}})

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        for waiter in self._status_waiters.values():
            if not waiter.done():
                waiter.cancel()
        self._status_waiters.clear()

    # ------------------------------------------------------------------ incoming

    def _on_datagram(self, payload: bytes, ip: str) -> None:
        parsed = protocol.parse_message(payload)
        if parsed is None:
            return
        cmd, data = parsed
        if cmd == "scan" and self._scan_results is not None:
            device_mac = str(data.get("device", ""))
            if device_mac:
                self._scan_results[device_mac] = {**data, "ip": data.get("ip") or ip}
        elif cmd == "devStatus":
            waiter = self._status_waiters.get(ip)
            if waiter is not None and not waiter.done():
                waiter.set_result(data)

    # ------------------------------------------------------------------ provider API

    async def discover(self, timeout_s: float) -> list[DiscoveredDevice]:
        transport = self._require_transport()
        async with self._scan_lock:
            self._scan_results = {}
            try:
                request = protocol.scan_request()
                # Repeat the multicast a few times: UDP may be lost on Wi-Fi.
                for _ in range(3):
                    transport.sendto(request, (protocol.MULTICAST_GROUP, protocol.SCAN_PORT))
                    await asyncio.sleep(min(0.5, timeout_s / 3))
                await asyncio.sleep(max(0.0, timeout_s - 1.5))
                results = dict(self._scan_results)
            finally:
                self._scan_results = None

        devices: list[DiscoveredDevice] = []
        for mac, data in results.items():
            sku = str(data.get("sku", "")) or None
            ip = str(data["ip"])
            friendly = protocol.KNOWN_MODELS.get(sku or "", sku or "Light")
            devices.append(
                DiscoveredDevice(
                    provider=self.key,
                    native_id=mac,
                    name=f"Govee {friendly}",
                    manufacturer=self.manufacturer,
                    model=sku,
                    address=ip,
                    capabilities=protocol.LAN_CAPABILITIES,
                    metadata={
                        "wifi_firmware": data.get("wifiVersionSoft"),
                        "wifi_hardware": data.get("wifiVersionHard"),
                        "ble_firmware": data.get("bleVersionSoft"),
                        "ble_hardware": data.get("bleVersionHard"),
                    },
                )
            )
        log.info("govee discovery finished", extra={"ctx": {"found": len(devices)}})
        return devices

    async def get_state(self, device: DeviceRecord) -> DeviceState:
        ip = self._require_address(device)
        async with self._lock(ip):
            return protocol.parse_status(await self._query_status(device, ip))

    async def execute(self, device: DeviceRecord, command: Command) -> DeviceState | None:
        ip = self._require_address(device)
        transport = self._require_transport()
        async with self._lock(ip):
            transport.sendto(protocol.command_request(command), (ip, protocol.CONTROL_PORT))
            state: DeviceState | None = None
            for delay in CONFIRM_DELAYS_S:
                await asyncio.sleep(delay)
                try:
                    state = protocol.parse_status(await self._query_status(device, ip, attempts=1))
                except DeviceTimeoutError:
                    continue
                if state_matches_command(command, state):
                    break
            if state is None:
                raise DeviceTimeoutError(
                    f"{device.name} did not answer after the command; its state is unknown",
                    details={"device_id": device.id, "address": ip},
                )
            return state

    # ------------------------------------------------------------------ helpers

    async def _query_status(
        self, device: DeviceRecord, ip: str, attempts: int = STATUS_ATTEMPTS
    ) -> dict[str, Any]:
        transport = self._require_transport()
        loop = asyncio.get_running_loop()
        for _ in range(attempts):
            waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._status_waiters[ip] = waiter
            try:
                transport.sendto(protocol.status_request(), (ip, protocol.CONTROL_PORT))
                return await asyncio.wait_for(waiter, STATUS_TIMEOUT_S)
            except TimeoutError:
                continue
            finally:
                if self._status_waiters.get(ip) is waiter:
                    del self._status_waiters[ip]
        raise DeviceTimeoutError(
            f"{device.name} did not answer the status request",
            details={"device_id": device.id, "address": ip},
        )

    def _lock(self, ip: str) -> asyncio.Lock:
        return self._ip_locks.setdefault(ip, asyncio.Lock())

    def _require_transport(self) -> asyncio.DatagramTransport:
        if self._transport is None:
            raise ProviderError("Govee LAN provider is not running")
        return self._transport

    @staticmethod
    def _require_address(device: DeviceRecord) -> str:
        if not device.address:
            raise DeviceOfflineError(
                f"{device.name} has no known IP address; run discovery",
                details={"device_id": device.id},
            )
        return device.address

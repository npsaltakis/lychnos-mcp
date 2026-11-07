"""eWeLink LAN provider (Sonoff devices on stock firmware, local control).

- Discovery and state: the device announces itself and its encrypted state over mDNS
  (``_ewelink._tcp``). The announcement is refreshed whenever the state changes, so no
  polling is needed and changes made from Google Home / eWeLink are picked up too.
- Control: HTTP POST ``http://<ip>:8081/zeroconf/<command>`` with an AES-encrypted payload.
  The HTTP answer only acknowledges receipt; the command counts as confirmed when the
  device announces the new state.
- Encryption keys are fetched once from the eWeLink Open API (see ``app.integrations``)
  and stored in the OS keyring (never in .env or the repository).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

import httpx
from zeroconf import IPVersion, ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

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
from app.providers.ewelink_lan import crypto
from app.providers.ewelink_lan.keystore import EwelinkDeviceInfo, KeyStore
from app.providers.ewelink_lan.models import NO_CAPABILITIES, UiidSpec, spec_for

log = logging.getLogger(__name__)

SERVICE_TYPE = "_ewelink._tcp.local."
HTTP_TIMEOUT_S = 2.0
CONFIRM_WAIT_S = 2.5
RESET_RETRIES = 3


@dataclass
class _Announcement:
    host: str  # ip:port
    seq: str | None
    encrypted: bool
    data: str | None  # base64 ciphertext (encrypted) or JSON text (DIY)
    iv: str | None
    seen_at: float = field(default_factory=time.monotonic)

    @property
    def ip(self) -> str:
        return self.host.rsplit(":", 1)[0]


class EwelinkLanProvider(DeviceProvider):
    key = "ewelink_lan"
    manufacturer = "Sonoff"

    def __init__(self, settings: Settings, keystore: KeyStore | None = None) -> None:
        self._settings = settings
        self._keystore = keystore or KeyStore(settings.data_dir)
        self._info: dict[str, EwelinkDeviceInfo] = {}
        self._seen: dict[str, _Announcement] = {}
        self._state: dict[str, DeviceState] = {}
        self._updated: dict[str, asyncio.Event] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._sink: ProviderEventSink | None = None
        self._azc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self, sink: ProviderEventSink) -> None:
        self._sink = sink
        self._info = self._keystore.load()
        interfaces: Any = [self._settings.lan_interface_ip] if self._settings.lan_interface_ip else None
        self._azc = AsyncZeroconf(interfaces=interfaces) if interfaces else AsyncZeroconf()
        self._browser = AsyncServiceBrowser(
            self._azc.zeroconf, SERVICE_TYPE, handlers=[self._on_service_change]
        )
        self._http = httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, headers={"Connection": "close"})
        missing = [d for d in self._info if not self._keystore.get_devicekey(d)]
        log.info(
            "ewelink lan started",
            extra={"ctx": {"configured_devices": len(self._info), "missing_keys": missing}},
        )

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._browser is not None:
            await self._browser.async_cancel()
        if self._azc is not None:
            await self._azc.async_close()
        if self._http is not None:
            await self._http.aclose()

    # ------------------------------------------------------------------ mDNS handling

    def _on_service_change(
        self, zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if not name.lower().startswith("ewelink_"):
            return
        deviceid = name[8:18]
        if state_change is ServiceStateChange.Removed:
            self._seen.pop(deviceid, None)
            log.info("ewelink device left the network", extra={"ctx": {"deviceid": deviceid}})
            return
        self._spawn(self._resolve(zeroconf, name, deviceid))

    async def _resolve(self, zeroconf: Zeroconf, name: str, deviceid: str) -> None:
        try:
            info = AsyncServiceInfo(SERVICE_TYPE, name)
            info.load_from_cache(zeroconf)
            if not info.properties or not info.parsed_addresses(IPVersion.V4Only):
                await info.async_request(zeroconf, 3000)
            addresses = info.parsed_addresses(IPVersion.V4Only)
            if not info.properties or not addresses:
                log.debug("incomplete ewelink announcement", extra={"ctx": {"deviceid": deviceid}})
                return
            props = {
                k.decode(): v.decode() if isinstance(v, bytes) else v
                for k, v in info.properties.items()
            }
            await self._handle_announcement(deviceid, f"{addresses[0]}:{info.port or 8081}", props)
        except Exception:
            log.exception("ewelink announcement failed", extra={"ctx": {"deviceid": deviceid}})

    async def _handle_announcement(self, deviceid: str, host: str, props: dict[str, Any]) -> None:
        data = "".join(props[f"data{i}"] for i in range(1, 5) if props.get(f"data{i}"))
        announcement = _Announcement(
            host=host,
            seq=props.get("seq"),
            encrypted=props.get("encrypt") == "true",
            data=data or None,
            iv=props.get("iv"),
        )
        previous = self._seen.get(deviceid)
        self._seen[deviceid] = announcement
        if self._sink is not None and previous is not None and previous.ip != announcement.ip:
            await self._sink.address_changed(self.key, deviceid, announcement.ip)

        params = self._decode(deviceid, announcement.data, announcement.iv, announcement.encrypted)
        if params is not None:
            await self._apply_params(deviceid, params)
        self._signal_update(deviceid)

    def _decode(
        self, deviceid: str, data: str | None, iv: str | None, encrypted: bool
    ) -> dict[str, Any] | None:
        if not data:
            return None
        if not encrypted:
            return json.loads(data)
        devicekey = self._keystore.get_devicekey(deviceid)
        if devicekey is None or iv is None:
            return None
        try:
            return crypto.decrypt(data, iv, devicekey)
        except Exception:
            log.warning("ewelink decrypt failed (wrong devicekey?)", extra={"ctx": {"deviceid": deviceid}})
            return None

    async def _apply_params(self, deviceid: str, params: dict[str, Any]) -> None:
        spec = self._spec_or_none(deviceid)
        if spec is None:
            return
        reported, full = spec.parse(params)
        current = self._state.get(deviceid, DeviceState())
        self._state[deviceid] = reported if full else current.merged_with(reported)
        if self._sink is not None:
            await self._sink.state_reported(self.key, deviceid, reported, partial=not full)

    def _signal_update(self, deviceid: str) -> None:
        event = self._updated.pop(deviceid, None)
        if event is not None:
            event.set()

    def _next_update(self, deviceid: str) -> asyncio.Event:
        return self._updated.setdefault(deviceid, asyncio.Event())

    # ------------------------------------------------------------------ provider API

    async def discover(self, timeout_s: float) -> list[DiscoveredDevice]:
        # The mDNS browser runs continuously; give late announcers a moment.
        await asyncio.sleep(min(timeout_s, 2.0))
        self._info = self._keystore.load()
        devices: list[DiscoveredDevice] = []
        for deviceid, announcement in self._seen.items():
            info = self._info.get(deviceid)
            spec = spec_for(info.uiid if info else None)
            has_key = self._keystore.get_devicekey(deviceid) is not None
            metadata: dict[str, Any] = {"uiid": info.uiid if info else None, "has_devicekey": has_key}
            if not has_key:
                metadata["setup_required"] = "Run the eWeLink key setup to enable local control"
            elif spec is None:
                metadata["setup_required"] = "This eWeLink device type is not supported yet"
            devices.append(
                DiscoveredDevice(
                    provider=self.key,
                    native_id=deviceid,
                    name=info.name if info else f"eWeLink {deviceid}",
                    manufacturer=(info.brand if info and info.brand else self.manufacturer),
                    model=info.model if info else None,
                    address=announcement.ip,
                    capabilities=spec.capabilities if (spec and has_key) else NO_CAPABILITIES,
                    metadata=metadata,
                )
            )
        return devices

    async def get_state(self, device: DeviceRecord) -> DeviceState:
        deviceid = device.native_id
        if deviceid not in self._seen:
            raise DeviceOfflineError(
                f"{device.name} is not announcing itself on the network",
                details={"device_id": device.id},
            )
        self._require_key(device)
        state = self._state.get(deviceid)
        if state is None:
            raise DeviceTimeoutError(
                f"{device.name} has not reported its state yet", details={"device_id": device.id}
            )
        return state

    async def execute(self, device: DeviceRecord, command: Command) -> DeviceState | None:
        deviceid = device.native_id
        announcement = self._seen.get(deviceid)
        if announcement is None:
            raise DeviceOfflineError(
                f"{device.name} is not announcing itself on the network",
                details={"device_id": device.id},
            )
        devicekey = self._require_key(device)
        spec = self._spec_or_none(deviceid)
        if spec is None:
            raise ProviderError(f"{device.name}: device type not supported", details={"device_id": device.id})

        async with self._locks.setdefault(deviceid, asyncio.Lock()):
            params = spec.encode(command, self._state.get(deviceid, DeviceState()))
            update = self._next_update(deviceid)
            response = await self._post(device, announcement.host, devicekey, params)

            # Some firmwares return the new state directly in the HTTP answer.
            if response.get("iv") and response.get("data"):
                returned = self._decode(deviceid, response["data"], response["iv"], True)
                if returned:
                    await self._apply_params(deviceid, returned)

            state = self._state.get(deviceid)
            if state is None or not state_matches_command(command, state):
                try:
                    await asyncio.wait_for(update.wait(), CONFIRM_WAIT_S)
                except TimeoutError:
                    log.info("ewelink: no state announcement after command", extra={"ctx": {"deviceid": deviceid}})
                    return None  # delivered, but not confirmed by the device
                state = self._state.get(deviceid)
            return state

    # ------------------------------------------------------------------ HTTP

    async def _post(
        self, device: DeviceRecord, host: str, devicekey: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        if self._http is None:
            raise ProviderError("eWeLink LAN provider is not running")
        command = next(iter(params))
        data, iv = crypto.encrypt(params, devicekey)
        payload = {
            "sequence": str(int(time.time() * 1000)),
            "deviceid": device.native_id,
            "selfApikey": "123",
            "iv": iv,
            "encrypt": True,
            "data": data,
        }
        url = f"http://{host}/zeroconf/{command}"
        for attempt in range(RESET_RETRIES + 1):
            try:
                response = await self._http.post(url, json=payload)
                body = response.json()
                break
            except (httpx.RemoteProtocolError, httpx.ReadError):
                # The device's web server handles one request at a time and resets others.
                if attempt == RESET_RETRIES:
                    raise ProviderError(f"{device.name} kept resetting the connection") from None
                await asyncio.sleep(0.15)
            except httpx.ConnectError as exc:
                raise DeviceOfflineError(
                    f"{device.name} refused the connection", details={"device_id": device.id}
                ) from exc
            except httpx.TimeoutException as exc:
                raise DeviceTimeoutError(
                    f"{device.name} did not answer", details={"device_id": device.id}
                ) from exc
            except ValueError as exc:
                raise ProviderError(f"{device.name} sent an invalid response") from exc
        error = body.get("error")
        if error != 0:
            raise ProviderError(
                f"{device.name} rejected the command (error {error})",
                details={"device_id": device.id, "device_error": error},
            )
        return body

    # ------------------------------------------------------------------ helpers

    def _require_key(self, device: DeviceRecord) -> str:
        devicekey = self._keystore.get_devicekey(device.native_id)
        if devicekey is None:
            raise ProviderError(
                f"{device.name}: no encryption key stored; run the eWeLink key setup",
                details={"device_id": device.id},
            )
        return devicekey

    def _spec_or_none(self, deviceid: str) -> UiidSpec | None:
        info = self._info.get(deviceid)
        return spec_for(info.uiid if info else None)

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

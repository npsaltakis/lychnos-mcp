"""Sends notifications about what happens in the home over the enabled channels.

Watches the event bus while at least one channel is enabled:
- a light goes offline / comes back online
- an automation fails (or runs, if wanted)
- a command fails (off by default: can be noisy)
Every channel is disabled until the user turns it on in Settings.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import base64
import logging
import time
from typing import Any

import httpx

from app.core.errors import InvalidCommandError
from app.core.secrets import SecretStore
from app.domain.notifications import (
    Notification,
    NotificationEvent,
    NotificationSettings,
    NotificationUpdate,
    NotificationView,
    PushSubscription,
)
from app.persistence.repositories import SettingsRepository
from app.services import notify_senders as senders
from app.services.device_service import DeviceService
from app.services.events import Event, EventBus, EventType

log = logging.getLogger(__name__)

SETTINGS_KEY = "notifications"
SUBSCRIPTIONS_KEY = "webpush_subscriptions"
SECRET_NAMES = {
    "ntfy_token": "notify:ntfy_token",
    "telegram_token": "notify:telegram_token",
    "smtp_password": "notify:smtp_password",
}
VAPID_SECRET = "notify:vapid_private_pem"
REPEAT_AFTER_S = 600  # the same alert for the same device at most every 10 minutes
CHANNELS = ("ntfy", "telegram", "email", "webpush")


class NotificationService:
    def __init__(
        self,
        settings_repo: SettingsRepository,
        secrets: SecretStore,
        bus: EventBus,
        devices: DeviceService,
        http: httpx.AsyncClient | None = None,
        smtp_factory: senders.SmtpFactory | None = None,
        webpush_fn: senders.WebPushFn | None = None,
    ) -> None:
        self._repo = settings_repo
        self._secrets = secrets
        self._bus = bus
        self._devices = devices
        self._http = http
        self._own_http = http is None
        self._smtp_factory = smtp_factory
        self._webpush_fn = webpush_fn
        self._settings = NotificationSettings()
        self._subscriptions: list[PushSubscription] = []
        self._watch_task: asyncio.Task[None] | None = None
        self._availability: dict[str, str] = {}
        self._last_sent: dict[tuple[str, str], float] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient()
        raw = await self._repo.get(SETTINGS_KEY)
        self._settings = NotificationSettings.model_validate(raw) if raw else NotificationSettings()
        raw_subs = await self._repo.get(SUBSCRIPTIONS_KEY) or []
        self._subscriptions = [PushSubscription.model_validate(s) for s in raw_subs]  # type: ignore[union-attr]
        self._ensure_watch()

    async def stop(self) -> None:
        for task in [self._watch_task, *self._tasks]:
            if task is not None:
                task.cancel()
        if self._own_http and self._http is not None:
            await self._http.aclose()

    # ------------------------------------------------------------------ settings

    def view(self) -> NotificationView:
        return NotificationView(
            **self._settings.model_dump(),
            secrets_set={key: bool(self._secrets.get(name)) for key, name in SECRET_NAMES.items()},
            webpush_devices=len(self._subscriptions),
        )

    async def update(self, data: NotificationUpdate) -> NotificationView:
        for key, value in data.secrets.model_dump().items():
            if value is None:
                continue
            if value == "":
                self._secrets.delete(SECRET_NAMES[key])
            else:
                self._secrets.set(SECRET_NAMES[key], value.strip())
        settings = NotificationSettings.model_validate(data.model_dump(exclude={"secrets"}))
        self._validate(settings)
        self._settings = settings
        await self._repo.set(SETTINGS_KEY, settings.model_dump())
        self._ensure_watch()
        return self.view()

    def _validate(self, s: NotificationSettings) -> None:
        problems = []
        if s.ntfy.enabled and not s.ntfy.topic:
            problems.append("ntfy: set a topic name")
        if s.telegram.enabled and not (self._secrets.get(SECRET_NAMES["telegram_token"]) and s.telegram.chat_id):
            problems.append("Telegram: set the bot token and the chat id")
        if s.email.enabled and not (s.email.smtp_host and s.email.sender and s.email.recipient):
            problems.append("Email: set SMTP server, sender and recipient")
        if problems:
            raise InvalidCommandError("; ".join(problems), details={"problems": problems})

    def telegram_bot_credentials(self) -> tuple[str, str] | None:
        """(token, chat id) when Telegram commands are turned on, else None."""
        t = self._settings.telegram
        token = self._secrets.get(SECRET_NAMES["telegram_token"])
        if t.enabled and t.commands and t.chat_id and token:
            return token, t.chat_id
        return None

    # ------------------------------------------------------------------ Web Push

    def vapid_public_key(self) -> str:
        """Browser-side application server key (base64url, uncompressed P-256 point)."""
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from py_vapid import Vapid02

        pem = self._secrets.get(VAPID_SECRET)
        if pem:
            vapid = Vapid02.from_pem(pem.encode())
        else:
            vapid = Vapid02()
            vapid.generate_keys()
            self._secrets.set(VAPID_SECRET, vapid.private_pem().decode())
        raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    async def add_subscription(self, sub: PushSubscription) -> NotificationView:
        self._subscriptions = [s for s in self._subscriptions if s.endpoint != sub.endpoint] + [sub]
        await self._save_subscriptions()
        return self.view()

    async def remove_subscription(self, endpoint: str) -> NotificationView:
        self._subscriptions = [s for s in self._subscriptions if s.endpoint != endpoint]
        await self._save_subscriptions()
        return self.view()

    async def _save_subscriptions(self) -> None:
        await self._repo.set(SUBSCRIPTIONS_KEY, [s.model_dump() for s in self._subscriptions])

    # ------------------------------------------------------------------ sending

    async def test(self, channel: str | None = None) -> dict[str, str]:
        """Send a test message to one channel (even if disabled) or to all enabled ones."""
        n = Notification(
            event=NotificationEvent.TEST,
            title="Lychnos: δοκιμή ειδοποίησης",
            message="Αν βλέπεις αυτό το μήνυμα, οι ειδοποιήσεις δουλεύουν.",
            tags=["bulb"],
        )
        channels = [channel] if channel else [c for c in CHANNELS if self._channel_enabled(c)]
        if not channels:
            raise InvalidCommandError("No notification channel is enabled")
        return await self._send(n, channels)

    async def telegram_chats(self, token: str | None = None) -> list[dict[str, str]]:
        token = (token or "").strip() or self._secrets.get(SECRET_NAMES["telegram_token"])
        if not token:
            raise InvalidCommandError("Enter the Telegram bot token first")
        assert self._http is not None
        try:
            return await senders.telegram_chats(self._http, token)
        except senders.SendError as exc:
            raise InvalidCommandError(str(exc)) from exc

    async def notify(self, n: Notification, *, dedupe_key: str | None = None) -> dict[str, str]:
        if n.event is not NotificationEvent.TEST and not getattr(self._settings.events, n.event.value):
            return {}
        if dedupe_key is not None:
            key = (n.event.value, dedupe_key)
            now = time.monotonic()
            if now - self._last_sent.get(key, -REPEAT_AFTER_S) < REPEAT_AFTER_S:
                return {}
            self._last_sent[key] = now
        channels = [c for c in CHANNELS if self._channel_enabled(c)]
        return await self._send(n, channels) if channels else {}

    def _channel_enabled(self, channel: str) -> bool:
        return bool(getattr(self._settings, channel).enabled)

    async def _send(self, n: Notification, channels: list[str]) -> dict[str, str]:
        results = await asyncio.gather(*(self._send_one(c, n) for c in channels), return_exceptions=True)
        report: dict[str, str] = {}
        for channel, result in zip(channels, results, strict=True):
            if isinstance(result, senders.SendError):
                report[channel] = str(result)
                log.warning("notification failed", extra={"ctx": {"channel": channel, "error": str(result)}})
            elif isinstance(result, BaseException):
                report[channel] = f"{channel}: unexpected error"
                log.error("notification crashed", exc_info=result, extra={"ctx": {"channel": channel}})
            else:
                report[channel] = "ok"
        return report

    async def _send_one(self, channel: str, n: Notification) -> None:
        assert self._http is not None
        s = self._settings
        if channel == "ntfy":
            await senders.send_ntfy(self._http, s.ntfy, self._secrets.get(SECRET_NAMES["ntfy_token"]), n)
        elif channel == "telegram":
            await senders.send_telegram(self._http, s.telegram, self._secrets.get(SECRET_NAMES["telegram_token"]), n)
        elif channel == "email":
            await senders.send_email(s.email, self._secrets.get(SECRET_NAMES["smtp_password"]), n, self._smtp_factory)
        elif channel == "webpush":
            pem = self._secrets.get(VAPID_SECRET)
            if not pem:
                raise senders.SendError("Web Push: enable notifications in a browser first")
            expired = await senders.send_webpush(self._subscriptions, pem, n, self._webpush_fn)
            if expired:
                self._subscriptions = [x for x in self._subscriptions if x.endpoint not in expired]
                await self._save_subscriptions()
                if not self._subscriptions:
                    raise senders.SendError("Web Push: the browser subscription expired; enable it again")
        else:
            raise senders.SendError(f"Unknown channel {channel!r}")

    # ------------------------------------------------------------------ event watching

    def _ensure_watch(self) -> None:
        running = self._watch_task is not None and not self._watch_task.done()
        if self._settings.any_enabled() and not running:
            # Remember the current availability so startup is not reported as a change.
            self._availability = {v.device.id: v.runtime.availability.value for v in self._devices.list_devices()}
            self._watch_task = asyncio.create_task(self._watch(), name="notifications")
        elif not self._settings.any_enabled() and running:
            assert self._watch_task is not None
            self._watch_task.cancel()
            self._watch_task = None

    async def _watch(self) -> None:
        with self._bus.subscribe() as queue:
            while True:
                event = await queue.get()
                try:
                    notification, key = self._translate(event)
                except Exception:
                    log.exception("notification event handling failed")
                    continue
                if notification is not None:
                    task = asyncio.create_task(self.notify(notification, dedupe_key=key))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

    def _translate(self, event: Event) -> tuple[Notification | None, str | None]:
        data = event.data
        if event.type is EventType.DEVICE_RUNTIME:
            device_id = data["device_id"]
            new = data["runtime"]["availability"]
            old = self._availability.get(device_id)
            self._availability[device_id] = new
            if old == new or old is None:
                return None, None
            try:
                device = self._devices.get_device(device_id)
            except Exception:
                return None, None
            if not device.registered:
                return None, None
            if new == "offline":
                return Notification(
                    event=NotificationEvent.DEVICE_OFFLINE,
                    title=f"{device.name}: εκτός δικτύου",
                    message=f"Η συσκευή {device.name} δεν απαντά. Μήπως κόπηκε το ρεύμα ή το Wi-Fi;",
                    tags=["warning"],
                    priority="high",
                ), device_id
            if new == "online" and old == "offline":
                return Notification(
                    event=NotificationEvent.DEVICE_ONLINE,
                    title=f"{device.name}: ξανά online",
                    message=f"Η συσκευή {device.name} απαντά ξανά.",
                    tags=["white_check_mark"],
                    priority="low",
                ), device_id
            return None, None
        if event.type is EventType.AUTOMATION_RAN:
            failed = data.get("outcome") == "failed"
            return Notification(
                event=NotificationEvent.AUTOMATION_FAILED if failed else NotificationEvent.AUTOMATION_RAN,
                title=f"Αυτοματισμός «{data.get('name')}»: {'απέτυχε' if failed else 'εκτελέστηκε'}",
                message=str(data.get("message", "")),
                tags=["x"] if failed else ["clock3"],
                priority="high" if failed else "low",
            ), (f"auto:{data.get('id')}" if failed else None)
        if event.type is EventType.WEIGHT_RECORDED and data.get("weighing"):
            kg = float(data["weighing"]["kg"])
            message = ""
            prev = data.get("previous")
            if prev:
                diff = kg - float(prev["kg"])
                when = datetime.fromisoformat(prev["ts"]).astimezone().strftime("%d/%m")
                sign = "+" if diff > 0 else "−" if diff < 0 else "±"
                message = f"{sign}{abs(diff):.1f} kg από τη ζύγιση της {when}".replace(".", ",")
            return Notification(
                event=NotificationEvent.WEIGHT,
                title=f"Ζύγιση: {kg:.1f} kg".replace(".", ","),
                message=message,
                tags=["scales"],
                priority="low",
            ), None
        if event.type is EventType.COMMAND_RESULT and data.get("outcome") == "failed":
            device_id = data.get("device_id", "")
            try:
                name = self._devices.get_device(device_id).name
            except Exception:
                name = device_id
            error = (data.get("error") or {}).get("message", "")
            return Notification(
                event=NotificationEvent.COMMAND_FAILED,
                title=f"{name}: η εντολή απέτυχε",
                message=error,
                tags=["x"],
            ), f"cmd:{device_id}"
        return None, None

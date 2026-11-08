"""Telegram bot that accepts commands from the configured chat.

Uses long polling (the backend asks Telegram for new messages), so nothing has to be reachable
from the internet. Only messages from the chat id set in Settings are answered; everything else
is ignored. Commands are logged with the source "telegram".

Commands (Greek or English, with or without "/"):
  menu / μενού              buttons for the common actions
  status / κατάσταση        which lights are on
  on [what] / άναψε [what]  what = light name, room name or nothing for all
  off [what] / σβήσε [what]
  50% [what] / φωτεινότητα 50 [what]
  scene <name> / σκηνή <name>, scenes / σκηνές
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import Any

import httpx

from app.core.errors import AppError
from app.domain.models import BatchResult, Command, CommandOutcome, SetBrightness, SetPower
from app.persistence.repositories import RoomRepository
from app.services.device_service import DeviceService
from app.services.notification_service import NotificationService
from app.services.notify_senders import TELEGRAM_API
from app.services.scene_service import SceneService

log = logging.getLogger(__name__)

SOURCE = "telegram"
POLL_TIMEOUT_S = 25
IDLE_S = 10.0

WORDS_ON = {"on", "αναψε", "ανοιξε", "αναμμα"}
WORDS_OFF = {"off", "σβησε", "κλεισε", "σβησιμο"}
WORDS_ALL = {"", "all", "ολα", "παντου"}
WORDS_STATUS = {"status", "κατασταση", "τι ειναι αναμμενο"}
WORDS_MENU = {"start", "help", "menu", "μενου", "βοηθεια"}
WORDS_SCENES = {"scenes", "σκηνες"}
WORDS_SCENE = {"scene", "σκηνη"}
WORDS_BRIGHT = {"bright", "brightness", "φωτεινοτητα", "dim"}

HELP = (
    "Lychnos 💡\n"
    "• άναψε / σβήσε [φως ή δωμάτιο] (χωρίς όνομα: όλα)\n"
    "• 40% [φως ή δωμάτιο]\n"
    "• σκηνή <όνομα>, σκηνές\n"
    "• κατάσταση\n"
    "• μενού"
)


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.strip().lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "💡 Άναμμα όλων", "callback_data": "on"}, {"text": "🌑 Σβήσιμο όλων", "callback_data": "off"}],
            [{"text": "📋 Κατάσταση", "callback_data": "status"}, {"text": "🎬 Σκηνές", "callback_data": "scenes"}],
        ]
    }


class TelegramBot:
    def __init__(
        self,
        notifications: NotificationService,
        devices: DeviceService,
        scenes: SceneService,
        rooms: RoomRepository,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._notifications = notifications
        self._devices = devices
        self._scenes = scenes
        self._rooms = rooms
        self._http = http
        self._own_http = http is None
        self._offset: int | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient()
        self._task = asyncio.create_task(self._loop(), name="telegram-bot")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._own_http and self._http is not None:
            await self._http.aclose()

    # ------------------------------------------------------------------ polling

    async def _loop(self) -> None:
        while True:
            creds = self._notifications.telegram_bot_credentials()
            if creds is None:
                self._offset = None
                await asyncio.sleep(IDLE_S)
                continue
            try:
                await self.poll_once(*creds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("telegram poll failed", extra={"ctx": {"error": exc.__class__.__name__}})
                await asyncio.sleep(IDLE_S)

    async def poll_once(self, token: str, chat_id: str, timeout_s: int = POLL_TIMEOUT_S) -> None:
        assert self._http is not None
        params: dict[str, Any] = {"timeout": timeout_s, "allowed_updates": '["message","callback_query"]'}
        if self._offset is not None:
            params["offset"] = self._offset
        response = await self._http.get(f"{TELEGRAM_API}/bot{token}/getUpdates", params=params, timeout=timeout_s + 10)
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", "telegram error"))
        for update in payload.get("result", []):
            self._offset = update["update_id"] + 1
            await self._handle_update(token, chat_id, update)

    async def _handle_update(self, token: str, chat_id: str, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            query = update["callback_query"]
            chat = str(((query.get("message") or {}).get("chat") or {}).get("id", ""))
            await self._api(token, "answerCallbackQuery", {"callback_query_id": query["id"]})
            if chat != chat_id:
                return
            data = query.get("data", "")
            text = f"scene-id {data[6:]}" if data.startswith("scene:") else data
        else:
            message = update.get("message") or {}
            if str((message.get("chat") or {}).get("id", "")) != chat_id:
                return  # not the owner: ignore silently
            text = message.get("text", "")
        reply, keyboard = await self.handle_text(text)
        body: dict[str, Any] = {"chat_id": chat_id, "text": reply}
        if keyboard:
            body["reply_markup"] = keyboard
        await self._api(token, "sendMessage", body)

    async def _api(self, token: str, method: str, body: dict[str, Any]) -> None:
        assert self._http is not None
        try:
            await self._http.post(f"{TELEGRAM_API}/bot{token}/{method}", json=body, timeout=10)
        except httpx.HTTPError as exc:
            log.warning("telegram send failed", extra={"ctx": {"method": method, "error": exc.__class__.__name__}})

    # ------------------------------------------------------------------ commands

    async def handle_text(self, raw: str) -> tuple[str, dict[str, Any] | None]:
        text = normalize(raw).lstrip("/")
        text = re.sub(r"@\w+", "", text).strip()  # "/on@MyBot"
        if text.startswith("scene-id "):
            return await self._scene(text[9:], by_id=True)
        first, _, rest = text.partition(" ")
        rest = rest.strip()
        try:
            if text in WORDS_MENU or first in WORDS_MENU:
                return HELP, menu_keyboard()
            if text in WORDS_STATUS or first in WORDS_STATUS:
                return self._status(), None
            if first in WORDS_ON:
                return await self._group(SetPower(on=True), rest, "Άναψαν"), None
            if first in WORDS_OFF:
                return await self._group(SetPower(on=False), rest, "Έσβησαν"), None
            pct = re.match(r"^(\d{1,3})\s*%\s*(.*)$", text)
            if first in WORDS_BRIGHT:
                pct = re.match(r"^(\d{1,3})\s*%?\s*(.*)$", rest)
            if pct:
                level = int(pct.group(1))
                if not 1 <= level <= 100:
                    return "Η φωτεινότητα είναι από 1 έως 100%.", None
                return await self._group(SetBrightness(brightness=level), pct.group(2).strip(), f"Στο {level}%"), None
            if text in WORDS_SCENES:
                return await self._scene_menu()
            if first in WORDS_SCENE:
                return await self._scene(rest)
            # A scene name on its own
            for scene in await self._scenes.list():
                if normalize(scene.name) == text:
                    return await self._scene(scene.id, by_id=True)
        except AppError as error:
            return f"⚠️ {error.message}", None
        return "Δεν το κατάλαβα. " + HELP, menu_keyboard()

    def _status(self) -> str:
        views = self._devices.list_devices(registered=True)
        if not views:
            return "Δεν υπάρχουν καταχωρημένα φώτα."
        lines = []
        for v in sorted(views, key=lambda v: v.device.name.lower()):
            s = v.runtime.state
            if v.runtime.availability.value == "offline":
                lines.append(f"⚪ {v.device.name}: εκτός σύνδεσης")
            elif s.power:
                lines.append(f"🟡 {v.device.name}: αναμμένο" + (f" {s.brightness}%" if s.brightness else ""))
            else:
                lines.append(f"⚫ {v.device.name}: σβηστό")
        return "\n".join(lines)

    async def _group(self, command: Command, target: str, verb: str) -> str:
        if target in WORDS_ALL:
            batch = await self._devices.execute_on_group(command, room_id=None, source=SOURCE)
            return self._summary(batch, verb, "όλα τα φώτα")
        for room in await self._rooms.list():
            if normalize(room.name) == target:
                batch = await self._devices.execute_on_group(command, room_id=room.id, source=SOURCE)
                return self._summary(batch, verb, room.name)
        views = self._devices.list_devices(registered=True)
        match = next((v for v in views if normalize(v.device.name) == target), None) or next(
            (v for v in views if target in normalize(v.device.name)), None
        )
        if match is None:
            names = ", ".join(v.device.name for v in views) or "—"
            return f"Δεν βρήκα «{target}». Φώτα: {names}"
        result = await self._devices.execute(match.device.id, command, source=SOURCE)
        return self._summary(BatchResult.build([result], []), verb, match.device.name)

    @staticmethod
    def _summary(batch: BatchResult, verb: str, what: str) -> str:
        if not batch.results:
            return f"Τίποτα να γίνει για {what}."
        ok = sum(1 for r in batch.results if r.outcome is CommandOutcome.CONFIRMED)
        failed = sum(1 for r in batch.results if r.outcome is CommandOutcome.FAILED)
        total = len(batch.results)
        if ok == total:
            return f"✅ {verb}: {what}"
        if failed == total:
            return f"❌ Απέτυχε: {what}"
        return f"⚠️ {verb}: {what} ({ok}/{total} επιβεβαιώθηκαν)"

    async def _scene_menu(self) -> tuple[str, dict[str, Any] | None]:
        scenes = await self._scenes.list()
        if not scenes:
            return "Δεν υπάρχουν σκηνές.", None
        rows = [[{"text": f"🎬 {s.name}", "callback_data": f"scene:{s.id}"}] for s in scenes[:20]]
        return "Διάλεξε σκηνή:", {"inline_keyboard": rows}

    async def _scene(self, ref: str, *, by_id: bool = False) -> tuple[str, dict[str, Any] | None]:
        scenes = await self._scenes.list()
        scene = next((s for s in scenes if (s.id == ref if by_id else normalize(s.name) == ref)), None)
        if scene is None and not by_id:
            scene = next((s for s in scenes if ref and ref in normalize(s.name)), None)
        if scene is None:
            return "Δεν βρήκα τη σκηνή. Σκηνές: " + (", ".join(s.name for s in scenes) or "—"), None
        batch = await self._scenes.activate(scene.id, source=SOURCE)
        return self._summary(batch, "Σκηνή", f"«{scene.name}»"), None

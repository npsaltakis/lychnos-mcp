"""Notification channels. Each sender raises ``SendError`` with a readable reason on failure."""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

import httpx

from app.domain.notifications import EmailConfig, Notification, NtfyConfig, PushSubscription, TelegramConfig

log = logging.getLogger(__name__)

NTFY_PRIORITY = {"low": 2, "default": 3, "high": 4}
TELEGRAM_API = "https://api.telegram.org"
# VAPID "sub" claim: py_vapid accepts only a mailto: address or a bare https://host (no path).
WEBPUSH_CONTACT = "https://github.com"


class SendError(Exception):
    pass


async def send_ntfy(http: httpx.AsyncClient, cfg: NtfyConfig, token: str | None, n: Notification) -> None:
    if not cfg.topic:
        raise SendError("ntfy: no topic set")
    # JSON publishing keeps UTF-8 (Greek) titles intact; header-based publishing would not.
    body = {
        "topic": cfg.topic,
        "title": n.title,
        "message": n.message,
        "tags": n.tags,
        "priority": NTFY_PRIORITY[n.priority],
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = await http.post(cfg.server.rstrip("/"), json=body, headers=headers, timeout=10)
    except httpx.HTTPError as exc:
        raise SendError(f"ntfy: cannot reach {cfg.server} ({exc.__class__.__name__})") from exc
    if response.status_code >= 400:
        raise SendError(f"ntfy: server answered HTTP {response.status_code}")


async def send_telegram(http: httpx.AsyncClient, cfg: TelegramConfig, token: str | None, n: Notification) -> None:
    if not token or not cfg.chat_id:
        raise SendError("Telegram: bot token and chat id are required")
    try:
        response = await http.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={"chat_id": cfg.chat_id, "text": f"{n.title}\n{n.message}", "disable_web_page_preview": True},
            timeout=10,
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SendError(f"Telegram: request failed ({exc.__class__.__name__})") from exc
    if not payload.get("ok"):
        raise SendError(f"Telegram: {payload.get('description', 'error')}")


async def telegram_chats(http: httpx.AsyncClient, token: str) -> list[dict[str, str]]:
    """Chats that recently wrote to the bot (the user sends /start first)."""
    try:
        payload = (await http.get(f"{TELEGRAM_API}/bot{token}/getUpdates", timeout=10)).json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SendError(f"Telegram: request failed ({exc.__class__.__name__})") from exc
    if not payload.get("ok"):
        raise SendError(f"Telegram: {payload.get('description', 'invalid token')}")
    chats: dict[str, str] = {}
    for update in payload.get("result", []):
        chat = (update.get("message") or update.get("my_chat_member") or {}).get("chat")
        if chat:
            name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("username", "")
            chats[str(chat["id"])] = name
    return [{"id": cid, "name": name} for cid, name in chats.items()]


SmtpFactory = Callable[[EmailConfig], smtplib.SMTP]


def _default_smtp(cfg: EmailConfig) -> smtplib.SMTP:
    if cfg.security == "ssl":
        return smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=15)
    smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15)
    if cfg.security == "starttls":
        smtp.starttls()
    return smtp


async def send_email(cfg: EmailConfig, password: str | None, n: Notification, factory: SmtpFactory | None = None) -> None:
    if not (cfg.smtp_host and cfg.sender and cfg.recipient):
        raise SendError("Email: SMTP server, sender and recipient are required")
    message = EmailMessage()
    message["Subject"] = n.title
    message["From"] = cfg.sender
    message["To"] = cfg.recipient
    message.set_content(n.message)

    def deliver() -> None:
        with (factory or _default_smtp)(cfg) as smtp:
            if cfg.username:
                smtp.login(cfg.username, password or "")
            smtp.send_message(message)

    try:
        await asyncio.to_thread(deliver)
    except smtplib.SMTPAuthenticationError as exc:
        raise SendError("Email: login failed (for Gmail use an App Password)") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise SendError(f"Email: {exc.__class__.__name__}: {exc}") from exc


WebPushFn = Callable[..., Any]


async def send_webpush(
    subscriptions: list[PushSubscription], private_pem: str, n: Notification, webpush_fn: WebPushFn | None = None
) -> list[str]:
    """Send to every subscribed browser. Returns endpoints that are gone and should be removed."""
    if not subscriptions:
        raise SendError("Web Push: no browser has enabled notifications yet")
    from py_vapid import Vapid02
    from pywebpush import WebPushException, webpush

    fn = webpush_fn or webpush
    vapid = Vapid02.from_pem(private_pem.encode())
    data = json.dumps({"title": n.title, "body": n.message, "tag": n.event.value})
    expired: list[str] = []
    errors: list[str] = []

    def push(sub: PushSubscription) -> None:
        try:
            fn(subscription_info=sub.model_dump(), data=data, vapid_private_key=vapid,
               vapid_claims={"sub": WEBPUSH_CONTACT}, ttl=3600, timeout=10)
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                expired.append(sub.endpoint)
            else:
                errors.append(f"HTTP {status}" if status else str(exc))
        except Exception as exc:  # signing, network, malformed subscription…
            log.warning("web push failed", extra={"ctx": {"error": repr(exc)}})
            errors.append(f"{exc.__class__.__name__}: {exc}")

    await asyncio.gather(*(asyncio.to_thread(push, s) for s in subscriptions))
    if errors and len(errors) + len(expired) == len(subscriptions):
        raise SendError(f"Web Push: delivery failed ({errors[0][:200]})")
    return expired

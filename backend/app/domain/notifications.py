"""Notification channels and which events trigger them. Every channel is off by default."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class NotificationEvent(StrEnum):
    DEVICE_OFFLINE = "device_offline"
    DEVICE_ONLINE = "device_online"
    AUTOMATION_FAILED = "automation_failed"
    AUTOMATION_RAN = "automation_ran"
    COMMAND_FAILED = "command_failed"
    WEIGHT = "weight"
    TEST = "test"


class NtfyConfig(BaseModel):
    enabled: bool = False
    server: str = Field("https://ntfy.sh", max_length=200, pattern=r"^https?://[^\s/]+(/[^\s]*)?$")
    topic: str = Field("", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")


class TelegramConfig(BaseModel):
    enabled: bool = False
    chat_id: str = Field("", max_length=32, pattern=r"^-?\d*$")
    commands: bool = Field(False, description="Also accept commands from this chat")


class EmailConfig(BaseModel):
    enabled: bool = False
    smtp_host: str = Field("", max_length=200)
    smtp_port: int = Field(587, ge=1, le=65535)
    security: Literal["starttls", "ssl", "none"] = "starttls"
    username: str = Field("", max_length=200)
    sender: str = Field("", max_length=200)
    recipient: str = Field("", max_length=200)


class WebPushConfig(BaseModel):
    enabled: bool = False


class EventToggles(BaseModel):
    device_offline: bool = True
    device_online: bool = True
    automation_failed: bool = True
    automation_ran: bool = False
    command_failed: bool = False
    weight: bool = True


class NotificationSettings(BaseModel):
    ntfy: NtfyConfig = Field(default_factory=NtfyConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    webpush: WebPushConfig = Field(default_factory=WebPushConfig)
    events: EventToggles = Field(default_factory=EventToggles)

    def any_enabled(self) -> bool:
        return self.ntfy.enabled or self.telegram.enabled or self.email.enabled or self.webpush.enabled


class SecretUpdates(BaseModel):
    """None = leave unchanged, "" = remove, anything else = new value. Never returned by the API."""

    ntfy_token: str | None = Field(None, max_length=200)
    telegram_token: str | None = Field(None, max_length=200)
    smtp_password: str | None = Field(None, max_length=200)


class NotificationUpdate(NotificationSettings):
    secrets: SecretUpdates = Field(default_factory=SecretUpdates)


class NotificationView(NotificationSettings):
    secrets_set: dict[str, bool]
    webpush_devices: int


class PushSubscription(BaseModel):
    endpoint: str = Field(max_length=1000, pattern=r"^https://")
    keys: dict[str, str]


class Notification(BaseModel):
    event: NotificationEvent
    title: str
    message: str
    tags: list[str] = Field(default_factory=list)
    priority: Literal["low", "default", "high"] = "default"

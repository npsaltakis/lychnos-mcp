"""Automations: a trigger (time of day or sunrise/sunset) and an action."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.models import Command, utcnow

ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]  # Monday = 0 … Sunday = 6
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _check_days(days: list[int]) -> list[int]:
    cleaned = sorted(set(days))
    if not cleaned or any(d < 0 or d > 6 for d in cleaned):
        raise ValueError("days must contain values 0 (Monday) … 6 (Sunday)")
    return cleaned


class TimeTrigger(BaseModel):
    type: Literal["time"] = "time"
    time: str = Field(description="Local time HH:MM")
    days: list[int] = Field(default_factory=lambda: list(ALL_DAYS))

    @field_validator("time")
    @classmethod
    def _time(cls, value: str) -> str:
        if not _TIME_RE.match(value):
            raise ValueError("time must be HH:MM (24h)")
        return value

    @field_validator("days")
    @classmethod
    def _days(cls, value: list[int]) -> list[int]:
        return _check_days(value)


class SunTrigger(BaseModel):
    type: Literal["sun"] = "sun"
    event: Literal["sunrise", "sunset"]
    offset_min: int = Field(0, ge=-240, le=240, description="Minutes before (<0) or after (>0)")
    days: list[int] = Field(default_factory=lambda: list(ALL_DAYS))

    @field_validator("days")
    @classmethod
    def _days(cls, value: list[int]) -> list[int]:
        return _check_days(value)


Trigger = Annotated[TimeTrigger | SunTrigger, Field(discriminator="type")]


class Target(BaseModel):
    kind: Literal["all", "room", "device"]
    id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Target:
        if self.kind != "all" and not self.id:
            raise ValueError(f"target kind {self.kind!r} needs an id")
        if self.kind == "all":
            self.id = None
        return self


class SceneActionSpec(BaseModel):
    type: Literal["scene"] = "scene"
    scene_id: str


class CommandActionSpec(BaseModel):
    type: Literal["command"] = "command"
    target: Target
    command: Command


class FadeActionSpec(BaseModel):
    """Wake-up light: switch on at 1 % and raise the brightness step by step."""

    type: Literal["fade"] = "fade"
    target: Target
    to_brightness: int = Field(100, ge=2, le=100)
    duration_min: int = Field(15, ge=1, le=120)
    color_temp_k: int | None = Field(None, ge=1000, le=12000, description="Optional white tone to use")


Action = Annotated[SceneActionSpec | CommandActionSpec | FadeActionSpec, Field(discriminator="type")]


class AutomationInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    trigger: Trigger
    action: Action


class Automation(AutomationInput):
    id: str
    last_run_at: datetime | None = None
    last_result: dict[str, Any] | None = None
    next_run_at: datetime | None = None  # computed, not stored
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Location(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str = Field("", max_length=80)

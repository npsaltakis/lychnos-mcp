"""Brand-independent domain models: devices, capabilities, state, commands, scenes."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- colours


_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


class RGB(BaseModel):
    model_config = ConfigDict(frozen=True)

    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)

    @classmethod
    def from_hex(cls, value: str) -> RGB:
        match = _HEX_RE.match(value.strip())
        if not match:
            raise ValueError(f"Invalid hex colour: {value!r}")
        raw = match.group(1)
        return cls(r=int(raw[0:2], 16), g=int(raw[2:4], 16), b=int(raw[4:6], 16))

    def to_hex(self) -> str:
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    def close_to(self, other: RGB, tolerance: int = 3) -> bool:
        return (
            abs(self.r - other.r) <= tolerance
            and abs(self.g - other.g) <= tolerance
            and abs(self.b - other.b) <= tolerance
        )


# --------------------------------------------------------------------------- capabilities


class Capability(StrEnum):
    POWER = "power"
    BRIGHTNESS = "brightness"
    COLOR_RGB = "color_rgb"
    COLOR_TEMP = "color_temp"


class ColorTempRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_k: int = Field(ge=1000, le=12000)
    max_k: int = Field(ge=1000, le=12000)

    @model_validator(mode="after")
    def _check(self) -> ColorTempRange:
        if self.min_k >= self.max_k:
            raise ValueError("min_k must be lower than max_k")
        return self


class Capabilities(BaseModel):
    """What a specific device verifiably supports. The UI and MCP only offer these."""

    model_config = ConfigDict(frozen=True)

    supported: frozenset[Capability] = frozenset()
    brightness_min: int = Field(1, ge=0, le=100)
    color_temp_range: ColorTempRange | None = None
    # Can the current state be read back from the device?
    supports_state_query: bool = False
    # Does the device announce state changes by itself (no polling needed)?
    supports_push: bool = False

    @model_validator(mode="after")
    def _check(self) -> Capabilities:
        if Capability.COLOR_TEMP in self.supported and self.color_temp_range is None:
            raise ValueError("color_temp capability requires color_temp_range")
        return self

    def has(self, capability: Capability) -> bool:
        return capability in self.supported


# --------------------------------------------------------------------------- state


class Availability(StrEnum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"


class LightMode(StrEnum):
    COLOR = "color"
    WHITE = "white"


class DeviceState(BaseModel):
    """A (possibly partial) light state. ``None`` means "not known / not reported"."""

    model_config = ConfigDict(frozen=True)

    power: bool | None = None
    brightness: int | None = Field(None, ge=0, le=100)
    color: RGB | None = None
    color_temp_k: int | None = Field(None, ge=1000, le=12000)
    mode: LightMode | None = None

    def merged_with(self, update: DeviceState) -> DeviceState:
        """Overlay the fields that ``update`` reports onto this state."""
        changes = update.model_dump(exclude_none=True)
        return self.model_copy(update={k: getattr(update, k) for k in changes})


# --------------------------------------------------------------------------- commands


class SetPower(BaseModel):
    type: Literal["set_power"] = "set_power"
    on: bool


class SetBrightness(BaseModel):
    type: Literal["set_brightness"] = "set_brightness"
    brightness: int = Field(ge=0, le=100, description="Percent")


class SetColor(BaseModel):
    type: Literal["set_color"] = "set_color"
    color: RGB


class SetColorTemperature(BaseModel):
    type: Literal["set_color_temperature"] = "set_color_temperature"
    kelvin: int = Field(ge=1000, le=12000)


Command = Annotated[
    SetPower | SetBrightness | SetColor | SetColorTemperature,
    Field(discriminator="type"),
]

REQUIRED_CAPABILITY: dict[type[BaseModel], Capability] = {
    SetPower: Capability.POWER,
    SetBrightness: Capability.BRIGHTNESS,
    SetColor: Capability.COLOR_RGB,
    SetColorTemperature: Capability.COLOR_TEMP,
}

COLOR_TEMP_TOLERANCE_K = 100
BRIGHTNESS_TOLERANCE = 1


def state_matches_command(command: Command, state: DeviceState) -> bool:
    """True if ``state`` (as reported by the device) reflects ``command``."""
    match command:
        case SetPower(on=on):
            return state.power is on
        case SetBrightness(brightness=value):
            return state.brightness is not None and abs(state.brightness - value) <= BRIGHTNESS_TOLERANCE
        case SetColor(color=color):
            return state.color is not None and state.color.close_to(color)
        case SetColorTemperature(kelvin=kelvin):
            return (
                state.color_temp_k is not None
                and abs(state.color_temp_k - kelvin) <= COLOR_TEMP_TOLERANCE_K
            )
    return False


class PendingCommand(BaseModel):
    command_id: str
    command: Command
    issued_at: datetime = Field(default_factory=utcnow)


class ErrorInfo(BaseModel):
    code: str
    message: str
    at: datetime = Field(default_factory=utcnow)


class CommandOutcome(StrEnum):
    CONFIRMED = "confirmed"  # device reported the requested state
    UNCONFIRMED = "unconfirmed"  # sent, but the device cannot report state back
    MISMATCH = "mismatch"  # device reported a different state after the command
    FAILED = "failed"  # not delivered (offline, timeout, error)


# --------------------------------------------------------------------------- devices


class DiscoveredDevice(BaseModel):
    """What a provider reports during discovery."""

    provider: str
    native_id: str
    name: str
    manufacturer: str
    model: str | None = None
    address: str | None = None
    capabilities: Capabilities
    metadata: dict[str, Any] = Field(default_factory=dict)


_ID_SANITIZE = re.compile(r"[^a-z0-9]+")


def make_device_id(provider: str, native_id: str) -> str:
    return f"{provider}-{_ID_SANITIZE.sub('', native_id.lower())}"


class DeviceRecord(BaseModel):
    """A persisted device. Only ``registered`` devices may be controlled."""

    id: str
    provider: str
    native_id: str
    name: str
    manufacturer: str
    model: str | None = None
    address: str | None = None
    registered: bool = False
    room_id: str | None = None
    capabilities: Capabilities
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DeviceRuntime(BaseModel):
    """Live, in-memory status of a device."""

    availability: Availability = Availability.UNKNOWN
    state: DeviceState = Field(default_factory=DeviceState, description="Last state confirmed by the device")
    state_confirmed_at: datetime | None = None
    pending: PendingCommand | None = None
    last_error: ErrorInfo | None = None
    consecutive_failures: int = 0
    last_seen_at: datetime | None = None


class RoomInput(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    icon: str = Field("home", max_length=24, pattern=r"^[a-z0-9-]+$")


class Room(RoomInput):
    id: str
    sort_order: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class ActivityEntry(BaseModel):
    id: int
    ts: datetime
    source: str  # "dashboard", "claude", "automation: <name>", "api"
    device_id: str | None
    device_name: str | None
    command: str
    outcome: str
    detail: str | None = None


class DeviceView(BaseModel):
    """API representation: persisted record + live runtime."""

    device: DeviceRecord
    runtime: DeviceRuntime


class CommandResult(BaseModel):
    command_id: str
    device_id: str
    command: Command
    outcome: CommandOutcome
    state: DeviceState
    availability: Availability
    error: ErrorInfo | None = None
    duration_ms: int


# --------------------------------------------------------------------------- scenes


class SceneAction(BaseModel):
    """Desired state of one device within a scene."""

    device_id: str
    power: bool | None = None
    brightness: int | None = Field(None, ge=0, le=100)
    color: RGB | None = None
    color_temp_k: int | None = Field(None, ge=1000, le=12000)
    # Extras, applied after the values above (at most one of them):
    gradient: list[RGB] | None = Field(None, min_length=2, max_length=6, description="RGBIC lights: colours along the light")
    gradient_per_bar: bool = True
    effect: Literal["rainbow", "breathe", "candle"] | None = None
    music_style: str | None = Field(None, max_length=40, description="Lights with music mode: the style to start")
    music_sensitivity: int = Field(80, ge=0, le=100)

    @model_validator(mode="after")
    def _check(self) -> SceneAction:
        if self.color is not None and self.color_temp_k is not None:
            raise ValueError("A scene action can set either color or color_temp_k, not both")
        extras = [x for x in (self.gradient, self.effect, self.music_style) if x is not None]
        if len(extras) > 1:
            raise ValueError("A scene action can use only one of gradient, effect or music")
        has_light_values = any(v is not None for v in (self.brightness, self.color, self.color_temp_k)) or bool(extras)
        if self.power is False and has_light_values:
            raise ValueError("power=false cannot be combined with brightness/colour values")
        if self.power is None and not has_light_values:
            raise ValueError("A scene action must set at least one value")
        return self

    def to_commands(self) -> list[Command]:
        if self.power is False:
            return [SetPower(on=False)]
        commands: list[Command] = []
        if self.power is True or self.power is None:
            # Turning on first makes colour/brightness changes visible on every brand.
            commands.append(SetPower(on=True))
        if self.color is not None:
            commands.append(SetColor(color=self.color))
        if self.color_temp_k is not None:
            commands.append(SetColorTemperature(kelvin=self.color_temp_k))
        if self.brightness is not None:
            commands.append(SetBrightness(brightness=self.brightness))
        return commands


class SceneInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field("", max_length=500)
    actions: list[SceneAction] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _unique_devices(self) -> SceneInput:
        ids = [a.device_id for a in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("Each device may appear only once in a scene")
        return self


class Scene(SceneInput):
    id: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SkippedDevice(BaseModel):
    device_id: str
    reason: str


class BatchOutcome(StrEnum):
    ALL_CONFIRMED = "all_confirmed"
    PARTIAL = "partial"  # some unconfirmed/mismatch/failed
    FAILED = "failed"  # nothing delivered
    NOTHING_TO_DO = "nothing_to_do"


class BatchResult(BaseModel):
    outcome: BatchOutcome
    results: list[CommandResult]
    skipped: list[SkippedDevice] = Field(default_factory=list)

    @classmethod
    def build(cls, results: list[CommandResult], skipped: list[SkippedDevice]) -> BatchResult:
        if not results:
            outcome = BatchOutcome.NOTHING_TO_DO
        elif all(r.outcome is CommandOutcome.CONFIRMED for r in results):
            outcome = BatchOutcome.ALL_CONFIRMED
        elif all(r.outcome is CommandOutcome.FAILED for r in results):
            outcome = BatchOutcome.FAILED
        else:
            outcome = BatchOutcome.PARTIAL
        return cls(outcome=outcome, results=results, skipped=skipped)


class Weighing(BaseModel):
    """One weighing from the bathroom scale."""

    id: int
    ts: datetime
    kg: float

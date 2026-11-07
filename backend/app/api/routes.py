"""REST endpoints. Everything except health and login requires authentication."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field

from app import __version__
from app.api.deps import (
    SESSION_COOKIE,
    Activity,
    Auth,
    Automations,
    Devices,
    Notifications,
    Rooms,
    Scenes,
    Source,
    origin_allowed,
    require_auth,
)
from app.domain.automations import Automation, AutomationInput, Location
from app.domain.notifications import NotificationUpdate, NotificationView, PushSubscription
from app.core.errors import CommandFailedError, UnauthorizedError
from app.domain.models import (
    ActivityEntry,
    BatchResult,
    Room,
    RoomInput,
    Command,
    CommandOutcome,
    CommandResult,
    DeviceRuntime,
    DeviceView,
    Scene,
    SceneInput,
    utcnow,
)

public = APIRouter(prefix="/api", tags=["system"])
api = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


# ----------------------------------------------------------------------------- system


@public.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# ----------------------------------------------------------------------------- dashboard login


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@public.get("/auth/status", tags=["auth"])
async def auth_status(request: Request, auth: Auth) -> dict[str, bool]:
    return {
        "authenticated": await auth.validate(request.cookies.get(SESSION_COOKIE)),
        "password_set": auth.password_set,
    }


@public.post("/auth/login", tags=["auth"])
async def login(body: LoginRequest, request: Request, response: Response, auth: Auth) -> dict[str, bool]:
    if not origin_allowed(request, request.headers.get("origin")):
        raise UnauthorizedError("Login is only allowed from the dashboard")
    session_id, expires = await auth.login(
        body.password,
        client=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", ""),
    )
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=int((expires - utcnow()).total_seconds()),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return {"authenticated": True}


@public.post("/auth/logout", tags=["auth"])
async def logout(request: Request, response: Response, auth: Auth) -> dict[str, bool]:
    await auth.logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict", httponly=True)
    return {"authenticated": False}


@api.get("/system/info", tags=["system"])
async def system_info(request: Request, devices: Devices) -> dict[str, object]:
    settings = request.app.state.settings
    views = devices.list_devices()
    return {
        "version": __version__,
        "providers": devices.provider_status(),
        "devices_total": len(views),
        "devices_registered": sum(1 for v in views if v.device.registered),
        "poll_interval_s": settings.poll_interval_s,
        "remote_access": settings.allow_remote,
    }


# ----------------------------------------------------------------------------- devices


class DiscoveryRequest(BaseModel):
    timeout_s: float | None = Field(None, gt=0, le=15)


class DeviceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    registered: bool | None = None
    room_id: str | None = Field(None, description="Room id; send null to remove the device from its room")


@api.post("/discovery/scan", tags=["devices"])
async def scan(devices: Devices, body: DiscoveryRequest | None = None) -> list[DeviceView]:
    return await devices.discover(body.timeout_s if body else None)


@api.get("/devices", tags=["devices"])
async def list_devices(devices: Devices, registered: bool | None = Query(None)) -> list[DeviceView]:
    return devices.list_devices(registered)


@api.get("/devices/{device_id}", tags=["devices"])
async def get_device(device_id: str, devices: Devices) -> DeviceView:
    return devices.get_view(device_id)


@api.patch("/devices/{device_id}", tags=["devices"])
async def update_device(device_id: str, body: DeviceUpdate, devices: Devices, rooms: Rooms) -> DeviceView:
    view = await devices.update_device(device_id, name=body.name, registered=body.registered)
    if "room_id" in body.model_fields_set:
        view = await rooms.assign(device_id, body.room_id)
    return view


@api.delete("/devices/{device_id}", tags=["devices"], status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(device_id: str, devices: Devices) -> None:
    await devices.delete_device(device_id)


@api.get("/devices/{device_id}/state", tags=["devices"])
async def get_state(
    device_id: str,
    devices: Devices,
    refresh: bool = Query(False, description="Read from the device instead of the cache"),
) -> DeviceRuntime:
    return await devices.refresh_state(device_id, force=refresh)


@api.post("/devices/{device_id}/commands", tags=["devices"])
async def send_command(device_id: str, command: Command, devices: Devices, source: Source) -> CommandResult:
    result = await devices.execute(device_id, command, source=source)
    if result.outcome is CommandOutcome.FAILED and result.error is not None:
        raise CommandFailedError(
            result.error.message,
            code=result.error.code,
            details={"result": result.model_dump(mode="json")},
        )
    return result


@api.post("/groups/all/commands", tags=["devices"])
async def send_group_command(command: Command, devices: Devices, source: Source) -> BatchResult:
    return await devices.execute_on_all(command, source=source)


# ----------------------------------------------------------------------------- scenes


@api.get("/scenes", tags=["scenes"])
async def list_scenes(scenes: Scenes) -> list[Scene]:
    return await scenes.list()


@api.post("/scenes", tags=["scenes"], status_code=status.HTTP_201_CREATED)
async def create_scene(body: SceneInput, scenes: Scenes) -> Scene:
    return await scenes.create(body)


@api.get("/scenes/{scene_id}", tags=["scenes"])
async def get_scene(scene_id: str, scenes: Scenes) -> Scene:
    return await scenes.get(scene_id)


@api.put("/scenes/{scene_id}", tags=["scenes"])
async def update_scene(scene_id: str, body: SceneInput, scenes: Scenes) -> Scene:
    return await scenes.update(scene_id, body)


@api.delete("/scenes/{scene_id}", tags=["scenes"], status_code=status.HTTP_204_NO_CONTENT)
async def delete_scene(scene_id: str, scenes: Scenes) -> None:
    await scenes.delete(scene_id)


@api.post("/scenes/{scene_id}/activate", tags=["scenes"])
async def activate_scene(scene_id: str, scenes: Scenes, source: Source) -> BatchResult:
    return await scenes.activate(scene_id, source=source)


# ----------------------------------------------------------------------------- rooms


@api.get("/rooms", tags=["rooms"])
async def list_rooms(rooms: Rooms) -> list[Room]:
    return await rooms.list()


@api.post("/rooms", tags=["rooms"], status_code=status.HTTP_201_CREATED)
async def create_room(body: RoomInput, rooms: Rooms) -> Room:
    return await rooms.create(body)


@api.put("/rooms/{room_id}", tags=["rooms"])
async def update_room(room_id: str, body: RoomInput, rooms: Rooms) -> Room:
    return await rooms.update(room_id, body)


@api.delete("/rooms/{room_id}", tags=["rooms"], status_code=status.HTTP_204_NO_CONTENT)
async def delete_room(room_id: str, rooms: Rooms) -> None:
    await rooms.delete(room_id)


@api.post("/rooms/{room_id}/commands", tags=["rooms"])
async def send_room_command(room_id: str, command: Command, rooms: Rooms, source: Source) -> BatchResult:
    return await rooms.command(room_id, command, source=source)


# ----------------------------------------------------------------------------- automations


class AutomationEnabled(BaseModel):
    enabled: bool


@api.get("/automations", tags=["automations"])
async def list_automations(automations: Automations) -> list[Automation]:
    return automations.list()


@api.post("/automations", tags=["automations"], status_code=status.HTTP_201_CREATED)
async def create_automation(body: AutomationInput, automations: Automations) -> Automation:
    return await automations.create(body)


@api.put("/automations/{automation_id}", tags=["automations"])
async def update_automation(automation_id: str, body: AutomationInput, automations: Automations) -> Automation:
    return await automations.update(automation_id, body)


@api.patch("/automations/{automation_id}", tags=["automations"])
async def set_automation_enabled(
    automation_id: str, body: AutomationEnabled, automations: Automations
) -> Automation:
    return await automations.set_enabled(automation_id, body.enabled)


@api.delete("/automations/{automation_id}", tags=["automations"], status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation(automation_id: str, automations: Automations) -> None:
    await automations.delete(automation_id)


@api.post("/automations/{automation_id}/run", tags=["automations"])
async def run_automation(automation_id: str, automations: Automations) -> Automation:
    return await automations.run_now(automation_id)


@api.get("/settings/location", tags=["automations"])
async def get_location(automations: Automations) -> Location | None:
    return automations.get_location()


@api.put("/settings/location", tags=["automations"])
async def set_location(body: Location, automations: Automations) -> Location:
    return await automations.set_location(body)


# ----------------------------------------------------------------------------- notifications


class TestRequest(BaseModel):
    channel: Literal["ntfy", "telegram", "email", "webpush"] | None = None


class TelegramChatsRequest(BaseModel):
    token: str | None = Field(None, max_length=200)


class Unsubscribe(BaseModel):
    endpoint: str = Field(max_length=1000)


@api.get("/notifications", tags=["notifications"])
async def get_notifications(notifications: Notifications) -> NotificationView:
    return notifications.view()


@api.put("/notifications", tags=["notifications"])
async def update_notifications(body: NotificationUpdate, notifications: Notifications) -> NotificationView:
    return await notifications.update(body)


@api.post("/notifications/test", tags=["notifications"])
async def test_notifications(notifications: Notifications, body: TestRequest | None = None) -> dict[str, str]:
    return await notifications.test(body.channel if body else None)


@api.post("/notifications/telegram/chats", tags=["notifications"])
async def telegram_chats(notifications: Notifications, body: TelegramChatsRequest | None = None) -> list[dict[str, str]]:
    return await notifications.telegram_chats(body.token if body else None)


@api.get("/notifications/webpush/key", tags=["notifications"])
async def webpush_key(notifications: Notifications) -> dict[str, str]:
    return {"public_key": notifications.vapid_public_key()}


@api.post("/notifications/webpush/subscriptions", tags=["notifications"])
async def webpush_subscribe(body: PushSubscription, notifications: Notifications) -> NotificationView:
    return await notifications.add_subscription(body)


@api.post("/notifications/webpush/unsubscribe", tags=["notifications"])
async def webpush_unsubscribe(body: Unsubscribe, notifications: Notifications) -> NotificationView:
    return await notifications.remove_subscription(body.endpoint)


# ----------------------------------------------------------------------------- history


@api.get("/activity", tags=["history"])
async def list_activity(
    activity: Activity,
    limit: int = Query(100, ge=1, le=1000),
    device_id: str | None = Query(None),
    kind: Literal["commands", "changes", "availability"] | None = Query(None),
) -> list[ActivityEntry]:
    return await activity.recent(limit, device_id, kind)

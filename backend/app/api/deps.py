"""Shared API dependencies: services and authentication.

Two ways to authenticate:
- ``Authorization: Bearer <api_token>`` for programs (MCP server, scripts). The token lives in
  the Windows user's data folder.
- A dashboard session cookie obtained by logging in with the dashboard password.
  Cookie requests that change something must come from our own dashboard origin (CSRF guard).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from app.core.errors import UnauthorizedError
from app.core.security import tokens_equal
from app.persistence.repositories import ActivityRepository
from app.services.auth_service import AuthService
from app.services.automation_service import AutomationService
from app.services.device_service import DeviceService
from app.services.notification_service import NotificationService
from app.services.room_service import RoomService
from app.services.scene_service import SceneService

SESSION_COOKIE = "shc_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def bearer_ok(request: Request, authorization: str | None) -> bool:
    if authorization is None or not authorization.startswith("Bearer "):
        return False
    return tokens_equal(authorization.removeprefix("Bearer ").strip(), request.app.state.api_token)


def origin_allowed(request: Request, origin: str | None) -> bool:
    return origin is not None and origin in request.app.state.settings.dashboard_origins


async def require_auth(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    if authorization is not None:
        if bearer_ok(request, authorization):
            # Programs may say who they are (the MCP server sends "claude"); shown in the history.
            client = request.headers.get("x-shc-client", "api")
            request.state.source = client if client.replace("-", "").isalnum() and len(client) <= 20 else "api"
            return
        raise UnauthorizedError("Invalid token")
    auth: AuthService = request.app.state.auth_service
    if not await auth.validate(request.cookies.get(SESSION_COOKIE)):
        raise UnauthorizedError("Απαιτείται σύνδεση")
    if request.method not in SAFE_METHODS and not origin_allowed(request, request.headers.get("origin")):
        raise UnauthorizedError("Cross-site request blocked")
    request.state.source = "dashboard"


def get_source(request: Request) -> str:
    return getattr(request.state, "source", "api")


def get_device_service(request: Request) -> DeviceService:
    service: DeviceService = request.app.state.device_service
    service.note_client_activity()
    return service


def get_scene_service(request: Request) -> SceneService:
    return request.app.state.scene_service


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_room_service(request: Request) -> RoomService:
    return request.app.state.room_service


def get_automation_service(request: Request) -> AutomationService:
    return request.app.state.automation_service


def get_activity_repo(request: Request) -> ActivityRepository:
    return request.app.state.activity_repo


def get_notification_service(request: Request) -> NotificationService:
    return request.app.state.notification_service


Devices = Annotated[DeviceService, Depends(get_device_service)]
Scenes = Annotated[SceneService, Depends(get_scene_service)]
Auth = Annotated[AuthService, Depends(get_auth_service)]
Rooms = Annotated[RoomService, Depends(get_room_service)]
Automations = Annotated[AutomationService, Depends(get_automation_service)]
Activity = Annotated[ActivityRepository, Depends(get_activity_repo)]
Notifications = Annotated[NotificationService, Depends(get_notification_service)]
Source = Annotated[str, Depends(get_source)]

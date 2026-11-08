"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app import __version__
from app.api import routes, websocket
from app.core.config import Settings, get_settings
from app.core.errors import AppError, NotFoundError
from app.core.logging import setup_logging
from app.core.passwords import PasswordStore
from app.core.security import load_or_create_token
from app.persistence.database import Database
from app.persistence.repositories import (
    ActivityRepository,
    AutomationRepository,
    PowerEventRepository,
    DeviceRepository,
    RoomRepository,
    SceneRepository,
    SettingsRepository,
    WeighingRepository,
)
from app.providers.base import DeviceProvider
from app.providers.registry import ProviderRegistry
from app.services.auth_service import AuthService
from app.core.secrets import KeyringSecretStore, SecretStore
from app.services.automation_service import AutomationService
from app.services.vacation_service import VacationService
from app.services.backup_service import BackupService
from app.services.telegram_bot import TelegramBot
from app.services.usage_service import UsageService
from app.services.presence_service import PresenceService
from app.services.effect_service import EffectService
from app.services.weather_service import WeatherService
from app.services.scale_service import ScaleService
from app.services.notification_service import NotificationService
from app.services.room_service import RoomService
from app.services.device_service import DeviceService
from app.services.events import EventBus
from app.services.scene_service import SceneService
from app.services.state_store import StateStore

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    providers: Iterable[DeviceProvider] | None = None,
    secret_store: SecretStore | None = None,
    presence_scanner: object | None = None,
    weather_enabled: bool = True,
    scale_scan: object | None = None,
) -> FastAPI:
    """Build the app. ``providers`` / ``secret_store`` override the real ones (used by tests)."""
    settings = settings or get_settings()
    secrets_backend: SecretStore = secret_store or KeyringSecretStore()
    settings.validate_exposure()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(settings.log_level, settings.log_dir if settings.log_to_file else None)
        app.state.settings = settings
        app.state.api_token = (
            settings.api_token.get_secret_value()
            if settings.api_token
            else load_or_create_token(settings.token_path)
        )

        db = Database(settings.db_path)
        await db.connect()
        registry = (
            ProviderRegistry(providers) if providers is not None else ProviderRegistry.from_settings(settings)
        )
        bus = EventBus()
        store = StateStore(bus, settings.offline_after_failures)
        activity_repo = ActivityRepository(db)
        room_repo = RoomRepository(db)
        device_service = DeviceService(settings, DeviceRepository(db), registry, store, bus, activity_repo)
        app.state.usage_service = UsageService(PowerEventRepository(db), device_service, store)
        effect_service = EffectService(device_service, bus, activity_repo)
        app.state.effect_service = effect_service
        scene_service = SceneService(SceneRepository(db), device_service, bus)
        scene_service.effects = effect_service
        room_service = RoomService(room_repo, device_service, bus)
        automation_service = AutomationService(
            AutomationRepository(db), SettingsRepository(db), room_repo, device_service, scene_service, bus
        )

        auth_service = AuthService(
            db, PasswordStore(settings.password_path), timedelta(hours=settings.session_ttl_hours)
        )
        await auth_service.purge_expired()
        if not auth_service.password_set:
            log.warning(
                "no dashboard password set; run: python -m app.auth_cli set-password",
                extra={"ctx": {"path": str(settings.password_path)}},
            )

        app.state.event_bus = bus
        app.state.device_service = device_service
        app.state.scene_service = scene_service
        app.state.auth_service = auth_service
        app.state.room_service = room_service
        app.state.automation_service = automation_service
        vacation_service = VacationService(
            SettingsRepository(db), device_service, bus, automation_service.get_location
        )
        app.state.vacation_service = vacation_service
        weather_service = WeatherService(
            automation_service.get_location, on_update=automation_service.weather_updated
        )
        automation_service.cloud_cover = weather_service.cloud_cover_at
        app.state.weather_service = weather_service
        backup_service = BackupService(db, settings.data_dir / "backups")
        app.state.backup_service = backup_service
        presence_service = PresenceService(
            SettingsRepository(db), device_service, scene_service, bus, automation_service.get_location,
            presence_scanner,
        )
        app.state.presence_service = presence_service
        scale_service = ScaleService(SettingsRepository(db), WeighingRepository(db), bus, scale_scan)  # type: ignore[arg-type]
        app.state.scale_service = scale_service
        app.state.activity_repo = activity_repo
        notification_service = NotificationService(
            SettingsRepository(db), secrets_backend, bus, device_service
        )
        app.state.notification_service = notification_service
        telegram_bot = TelegramBot(notification_service, device_service, scene_service, room_repo)
        app.state.telegram_bot = telegram_bot

        await device_service.start()
        await automation_service.start()
        await vacation_service.start()
        await backup_service.start()
        await presence_service.start()
        await scale_service.start()
        if weather_enabled:
            await weather_service.start()
        await notification_service.start()
        await telegram_bot.start()
        log.info(
            "backend started",
            extra={"ctx": {"host": settings.host, "port": settings.port, "version": __version__}},
        )
        try:
            yield
        finally:
            await telegram_bot.stop()
            await effect_service.stop(log_it=False)
            await notification_service.stop()
            await presence_service.stop()
            await scale_service.stop()
            await weather_service.stop()
            await backup_service.stop()
            await vacation_service.stop()
            await automation_service.stop()
            await device_service.stop()
            await db.close()
            log.info("backend stopped")

    app = FastAPI(title="Lychnos", version=__version__, lifespan=lifespan)

    # Reject requests whose Host header is not ours (protects against DNS rebinding).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_headers)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Invalid request",
                    "details": {"errors": jsonable_errors(exc)},
                }
            },
        )

    app.include_router(routes.public)
    app.include_router(routes.api)
    app.include_router(websocket.router)
    mount_dashboard(app, settings.frontend_dist)
    return app


def mount_dashboard(app: FastAPI, dist: Path) -> None:
    """Serve the built React dashboard with SPA fallback (if it has been built)."""
    index = dist / "index.html"
    if not index.is_file():
        log.info("dashboard not built; serving API only", extra={"ctx": {"dist": str(dist)}})
        return
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def dashboard(path: str) -> FileResponse:
        if path.startswith("api/") or path == "api":
            raise NotFoundError("Unknown API route", details={"path": f"/{path}"})
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def jsonable_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    return [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]

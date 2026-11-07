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
from app.persistence.repositories import DeviceRepository, SceneRepository
from app.providers.base import DeviceProvider
from app.providers.registry import ProviderRegistry
from app.services.auth_service import AuthService
from app.services.device_service import DeviceService
from app.services.events import EventBus
from app.services.scene_service import SceneService
from app.services.state_store import StateStore

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    providers: Iterable[DeviceProvider] | None = None,
) -> FastAPI:
    """Build the app. ``providers`` overrides the configured providers (used by tests)."""
    settings = settings or get_settings()
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
        device_service = DeviceService(settings, DeviceRepository(db), registry, store, bus)
        scene_service = SceneService(SceneRepository(db), device_service, bus)

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

        await device_service.start()
        log.info(
            "backend started",
            extra={"ctx": {"host": settings.host, "port": settings.port, "version": __version__}},
        )
        try:
            yield
        finally:
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

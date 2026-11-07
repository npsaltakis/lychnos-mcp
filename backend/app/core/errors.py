"""Application error hierarchy. Every error maps to a stable code and HTTP status."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class DeviceNotRegisteredError(AppError):
    status_code = 409
    code = "device_not_registered"


class InvalidCommandError(AppError):
    status_code = 422
    code = "invalid_command"


class CapabilityNotSupportedError(AppError):
    status_code = 422
    code = "capability_not_supported"


class ProviderError(AppError):
    """The device integration failed in an unexpected way."""

    status_code = 502
    code = "provider_error"


class ProviderUnavailableError(AppError):
    status_code = 503
    code = "provider_unavailable"


class DeviceOfflineError(AppError):
    status_code = 503
    code = "device_offline"


class DeviceTimeoutError(AppError):
    status_code = 504
    code = "device_timeout"


_FAILURE_STATUS = {
    DeviceOfflineError.code: DeviceOfflineError.status_code,
    DeviceTimeoutError.code: DeviceTimeoutError.status_code,
    ProviderUnavailableError.code: ProviderUnavailableError.status_code,
}


class CommandFailedError(AppError):
    """A validated command could not be delivered; keeps the original error code."""

    def __init__(self, message: str, *, code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details=details)
        self.code = code
        self.status_code = _FAILURE_STATUS.get(code, ProviderError.status_code)

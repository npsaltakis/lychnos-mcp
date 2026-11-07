"""Application settings (environment variables with the SHC_ prefix, or backend/.env)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
LOOPBACK_HOST_HEADERS = ("127.0.0.1", "localhost", "[::1]")


def _default_data_dir() -> Path:
    """Per-user folder for the database, logs, API token and password hash.

    Windows: %USERPROFILE%\\.smarthome-control. Not under AppData on purpose: the Claude desktop app
    (and other MSIX-packaged apps) redirects AppData writes of the programs it starts into a private
    copy, which would give the backend and the MCP server different folders depending on who started them.
    Linux/macOS: $XDG_DATA_HOME/SmartHomeControl (default ~/.local/share/SmartHomeControl).
    Override with SHC_DATA_DIR. The MCP server uses the same rules (smarthome_mcp/client.py).
    """
    if os.name == "nt":
        return Path.home() / ".smarthome-control"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "SmartHomeControl"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SHC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- network / exposure
    host: str = "127.0.0.1"
    port: int = Field(3000, ge=1, le=65535)
    allow_remote: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )

    # Built dashboard (npm run build). Served at "/" when it exists.
    frontend_dist: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "frontend" / "dist"
    )

    # --- storage / logging
    data_dir: Path = Field(default_factory=_default_data_dir)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_to_file: bool = True
    # Optional override of the generated API token (mainly for tests / automation).
    api_token: SecretStr | None = None
    # Dashboard login session lifetime.
    session_ttl_hours: int = Field(168, ge=1, le=24 * 90)

    # --- devices
    enabled_providers: list[str] = Field(default_factory=list)
    command_timeout_s: float = Field(5.0, gt=0, le=30)
    # IPv4 of the PC's LAN interface for multicast discovery (auto-detected when empty).
    lan_interface_ip: str | None = None
    discovery_timeout_s: float = Field(3.0, gt=0, le=30)
    poll_interval_s: float = Field(30.0, ge=1)
    # Keep polling devices without push even when no dashboard is open (needed for the change log).
    poll_always: bool = True
    state_max_age_s: float = Field(5.0, ge=0)
    offline_after_failures: int = Field(3, ge=1)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "smarthome.db"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "api_token"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK_HOSTS

    @property
    def trusted_host_headers(self) -> list[str]:
        return [*LOOPBACK_HOST_HEADERS, *self.allowed_hosts]

    @property
    def password_path(self) -> Path:
        return self.data_dir / "dashboard_password.json"

    @property
    def dashboard_origins(self) -> set[str]:
        """Browser origins allowed to log in and send cookie-authenticated changes."""
        origins = {*self.cors_origins, f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}
        for host in self.allowed_hosts:
            origins |= {f"https://{host}", f"http://{host}:{self.port}"}
        return origins

    def validate_exposure(self) -> None:
        """Refuse to bind outside loopback unless remote access was explicitly enabled."""
        if not self.is_loopback and not self.allow_remote:
            raise ValueError(
                f"Refusing to bind to {self.host!r}: set SHC_ALLOW_REMOTE=true to expose the API "
                "outside localhost (bearer-token authentication stays mandatory)."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()

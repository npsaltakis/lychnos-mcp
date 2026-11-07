"""Local API token handling.

A random token is generated on first start and stored in the per-user data directory
(%USERPROFILE%\\.smarthome-control\\api_token on Windows, ~/.local/share/SmartHomeControl/api_token
on Linux), which only the current user can read.
Every API call (REST, WebSocket, MCP server) must present it as a bearer token.
"""

from __future__ import annotations

import contextlib
import hmac
import secrets
from pathlib import Path

MIN_TOKEN_LENGTH = 32


def load_or_create_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= MIN_TOKEN_LENGTH:
            return token
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)  # Linux/macOS: owner-only; on Windows the profile folder is already private
    return token


def tokens_equal(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))

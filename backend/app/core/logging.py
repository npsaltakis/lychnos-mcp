"""Structured JSON logging to stderr and a rotating file.

Usage: ``log.info("command executed", extra={"ctx": {"device_id": ..., "outcome": ...}})``
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict):
            payload.update(ctx)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


_configured = False


def setup_logging(level: str, log_dir: Path | None) -> None:
    """Configure the root logger once. Logs go to stderr (never stdout) and optionally a file."""
    global _configured
    root = logging.getLogger()
    root.setLevel(level)
    if _configured:
        return
    formatter = JsonFormatter()

    # pythonw.exe (background start on Windows) has no console: sys.stderr is None.
    if sys.stderr is not None:
        stderr = logging.StreamHandler(sys.stderr)
        stderr.setFormatter(formatter)
        root.addHandler(stderr)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "backend.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    for noisy in ("uvicorn.access", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True

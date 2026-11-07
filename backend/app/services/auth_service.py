"""Dashboard login: password check, brute-force throttling and server-side sessions.

Session ids are random 256-bit values given to the browser in an HttpOnly cookie.
Only their SHA-256 is stored, so a copy of the database does not contain usable sessions.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.errors import AppError, UnauthorizedError
from app.core.passwords import PasswordStore
from app.domain.models import utcnow
from app.persistence.database import Database

log = logging.getLogger(__name__)

MAX_FAILURES = 5
BASE_LOCKOUT_S = 30
MAX_LOCKOUT_S = 15 * 60


class TooManyAttemptsError(AppError):
    status_code = 429
    code = "too_many_attempts"


class PasswordNotSetError(AppError):
    status_code = 409
    code = "password_not_set"


@dataclass
class _Throttle:
    failures: int = 0
    locked_until: float = 0.0
    lockouts: int = 0


def _hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


class AuthService:
    def __init__(self, db: Database, passwords: PasswordStore, session_ttl: timedelta) -> None:
        self._db = db
        self._passwords = passwords
        self._ttl = session_ttl
        self._throttle: dict[str, _Throttle] = {}
        self._lock = asyncio.Lock()

    @property
    def password_set(self) -> bool:
        return self._passwords.is_set()

    async def login(self, password: str, client: str, user_agent: str) -> tuple[str, datetime]:
        if not self._passwords.is_set():
            raise PasswordNotSetError("No dashboard password has been set yet")
        throttle = self._throttle.setdefault(client, _Throttle())
        now = time.monotonic()
        if throttle.locked_until > now:
            wait = int(throttle.locked_until - now) + 1
            raise TooManyAttemptsError(
                f"Πολλές λάθος προσπάθειες. Δοκίμασε ξανά σε {wait} δευτερόλεπτα.",
                details={"retry_after_s": wait},
            )
        # scrypt is deliberately slow; run it off the event loop and one at a time.
        async with self._lock:
            ok = await asyncio.to_thread(self._passwords.verify, password)
        if not ok:
            throttle.failures += 1
            if throttle.failures >= MAX_FAILURES:
                throttle.lockouts += 1
                throttle.failures = 0
                throttle.locked_until = now + min(MAX_LOCKOUT_S, BASE_LOCKOUT_S * 2 ** (throttle.lockouts - 1))
            log.warning("dashboard login failed", extra={"ctx": {"client": client}})
            raise UnauthorizedError("Λάθος κωδικός")
        self._throttle.pop(client, None)

        session_id = secrets.token_urlsafe(32)
        created = utcnow()
        expires = created + self._ttl
        await self._db.conn.execute(
            "INSERT INTO sessions (id_hash, created_at, expires_at, user_agent) VALUES (?, ?, ?, ?)",
            (_hash(session_id), created.isoformat(), expires.isoformat(), user_agent[:200]),
        )
        await self._db.conn.commit()
        log.info("dashboard login", extra={"ctx": {"client": client}})
        return session_id, expires

    async def validate(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        async with self._db.conn.execute(
            "SELECT expires_at FROM sessions WHERE id_hash = ?", (_hash(session_id),)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return False
        if datetime.fromisoformat(row[0]) <= utcnow():
            await self.logout(session_id)
            return False
        return True

    async def logout(self, session_id: str | None) -> None:
        if not session_id:
            return
        await self._db.conn.execute("DELETE FROM sessions WHERE id_hash = ?", (_hash(session_id),))
        await self._db.conn.commit()

    async def purge_expired(self) -> None:
        await self._db.conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (utcnow().isoformat(),))
        await self._db.conn.commit()

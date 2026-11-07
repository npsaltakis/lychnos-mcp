"""Dashboard password: scrypt hash stored in the per-user data folder (never in the repo)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path

MIN_LENGTH = 8
_N, _R, _P, _DKLEN = 2**15, 8, 1, 32
_MAXMEM = 128 * _N * _R * 2


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_DKLEN, maxmem=_MAXMEM)


def hash_password(password: str) -> dict[str, object]:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"Password must be at least {MIN_LENGTH} characters")
    salt = os.urandom(16)
    return {
        "scheme": "scrypt",
        "n": _N,
        "r": _R,
        "p": _P,
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(_derive(password, salt, _N, _R, _P)).decode(),
    }


def verify_password(password: str, record: dict[str, object]) -> bool:
    try:
        salt = base64.b64decode(str(record["salt"]))
        expected = base64.b64decode(str(record["hash"]))
        actual = _derive(password, salt, int(record["n"]), int(record["r"]), int(record["p"]))  # type: ignore[arg-type]
    except (KeyError, ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


class PasswordStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def is_set(self) -> bool:
        return self._path.is_file()

    def set(self, password: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(hash_password(password)), encoding="utf-8")

    def verify(self, password: str) -> bool:
        if not self.is_set():
            return False
        return verify_password(password, json.loads(self._path.read_text(encoding="utf-8")))

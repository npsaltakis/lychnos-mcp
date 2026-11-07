"""eWeLink LAN payload encryption: AES-128-CBC, key = MD5(devicekey), PKCS7 padding, base64."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def _key(devicekey: str) -> bytes:
    return hashlib.md5(devicekey.encode("utf-8")).digest()  # noqa: S324 - protocol-mandated


def encrypt(data: dict[str, Any], devicekey: str, iv: bytes | None = None) -> tuple[str, str]:
    """Return (ciphertext_b64, iv_b64) for a JSON-serialisable dict."""
    iv = iv or os.urandom(16)
    plaintext = json.dumps(data, separators=(",", ":")).encode("utf-8")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_key(devicekey)), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode(), base64.b64encode(iv).decode()


def decrypt(data_b64: str, iv_b64: str, devicekey: str) -> dict[str, Any]:
    decryptor = Cipher(
        algorithms.AES(_key(devicekey)), modes.CBC(base64.b64decode(iv_b64))
    ).decryptor()
    padded = decryptor.update(base64.b64decode(data_b64)) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    result = json.loads(plaintext.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Decrypted payload is not a JSON object")
    return result

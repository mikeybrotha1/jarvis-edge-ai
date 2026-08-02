"""Signing-secret encryption at rest (Fernet, env-backed key).

Requires ``JARVIS_NOTIFICATIONS_ENCRYPTION_KEY`` (Fernet url-safe base64 key).
Never log decrypted secrets.
"""

from __future__ import annotations

import os
from typing import Mapping

from cryptography.fernet import Fernet, InvalidToken


class SecretEncryptionError(RuntimeError):
    """Raised when encryption key is missing or ciphertext is invalid."""


def generate_encryption_key() -> str:
    """Return a new Fernet key suitable for JARVIS_NOTIFICATIONS_ENCRYPTION_KEY."""

    return Fernet.generate_key().decode("ascii")


def encrypt_secret(
    plaintext: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    if not plaintext:
        raise SecretEncryptionError("Signing secret must be non-empty.")
    fernet = _fernet(environ)
    token = fernet.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(
    ciphertext: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    if not ciphertext:
        raise SecretEncryptionError("Missing encrypted secret.")
    fernet = _fernet(environ)
    try:
        return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as error:
        raise SecretEncryptionError(
            "Unable to decrypt signing secret (wrong key or corrupt data)."
        ) from error


def encryption_key_available(
    environ: Mapping[str, str] | None = None,
) -> bool:
    env = environ if environ is not None else os.environ
    key = env.get("JARVIS_NOTIFICATIONS_ENCRYPTION_KEY", "").strip()
    if not key:
        return False
    try:
        Fernet(key.encode("ascii") if isinstance(key, str) else key)
        return True
    except Exception:  # noqa: BLE001
        return False


def _fernet(environ: Mapping[str, str] | None) -> Fernet:
    env = environ if environ is not None else os.environ
    key = env.get("JARVIS_NOTIFICATIONS_ENCRYPTION_KEY", "").strip()
    if not key:
        raise SecretEncryptionError(
            "Signing secrets require JARVIS_NOTIFICATIONS_ENCRYPTION_KEY "
            "(Fernet key). Generate with: python -c "
            "\"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(key.encode("ascii"))
    except Exception as error:  # noqa: BLE001
        raise SecretEncryptionError(
            "JARVIS_NOTIFICATIONS_ENCRYPTION_KEY is not a valid Fernet key."
        ) from error

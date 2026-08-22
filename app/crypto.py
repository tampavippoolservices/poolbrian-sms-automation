from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class TokenEncryptionError(RuntimeError):
    pass


def _cipher() -> Fernet:
    key = os.getenv("TOKEN_ENCRYPTION_KEY")
    if not key:
        raise TokenEncryptionError("TOKEN_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (TypeError, ValueError) as exc:
        raise TokenEncryptionError("TOKEN_ENCRYPTION_KEY is invalid") from exc


def encrypt_secret(value: str) -> str:
    return _cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise TokenEncryptionError("Stored token cannot be decrypted") from exc

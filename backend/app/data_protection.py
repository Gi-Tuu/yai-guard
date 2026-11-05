"""Optional field-level encryption for task text stored in SQLite/backups.

Trace payloads are redacted independently. This protects the raw task prompt,
summary and error at rest when ``TASK_DATA_KEY`` is configured. Legacy plaintext
rows remain readable; new writes use the ``enc:v1:`` prefix.
"""
from __future__ import annotations

import os
from typing import Any

_PREFIX = "enc:v1:"
_fernet: Any | None = None


class DataProtectionError(RuntimeError):
    pass


def configure(key: str | None = None) -> bool:
    """Configure the Fernet key. Returns True when encryption is active."""
    global _fernet
    raw = key if key is not None else os.environ.get("TASK_DATA_KEY")
    if not raw:
        _fernet = None
        return False
    try:
        from cryptography.fernet import Fernet

        _fernet = Fernet(raw.encode("ascii"))
    except Exception as exc:  # noqa: BLE001 - fail closed when a key is declared
        _fernet = None
        raise DataProtectionError(f"invalid TASK_DATA_KEY: {type(exc).__name__}") from exc
    return True


def enabled() -> bool:
    return _fernet is not None


def protect(value: str | None) -> str | None:
    if value is None or _fernet is None:
        return value
    token = _fernet.encrypt(value.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def unprotect(value: str | None) -> str | None:
    if value is None or not isinstance(value, str) or not value.startswith(_PREFIX):
        return value
    if _fernet is None:
        raise DataProtectionError("encrypted task data found but TASK_DATA_KEY is not configured")
    from cryptography.fernet import InvalidToken

    try:
        return _fernet.decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise DataProtectionError("TASK_DATA_KEY cannot decrypt task data") from exc


configure()

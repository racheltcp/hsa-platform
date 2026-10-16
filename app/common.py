"""Small helpers shared by accounts, cards and purchases."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from .errors import ValidationError


def clock() -> datetime:
    """Current UTC time. Always call it as common.clock() so tests can replace it
    to simulate card expiry or a new tax year."""
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return clock().isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def require_text(data: dict, field: str, label: str, max_len: int = 100) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} is required.", field=field)
    value = value.strip()
    if len(value) > max_len:
        raise ValidationError(f"{label} must be {max_len} characters or fewer.", field=field)
    return value


def idempotency_key(data: dict) -> str:
    """The client's retry key, or a fresh one if the client didn't send any."""
    key = data.get("idempotency_key")
    if key is None or key == "":
        return secrets.token_hex(16)
    if not isinstance(key, str) or len(key) > 100:
        raise ValidationError("idempotency_key must be a string of 100 characters or fewer.",
                              field="idempotency_key")
    return key

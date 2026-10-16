"""Money parsing and formatting. All amounts are integer cents internally."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .errors import ValidationError

# A sanity ceiling for a single deposit or purchase in this simulation.
MAX_AMOUNT_CENTS = 100_000_00


def parse_amount(value, field: str = "amount") -> int:
    """Convert a dollar amount ("12.34", 12.34, or 12) to positive integer cents.

    Rejects floats' binary noise by going through str -> Decimal, and rejects
    sub-cent precision instead of silently rounding.
    """
    if value is None or isinstance(value, bool):
        raise ValidationError(f"{field} is required")
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{field} must be a number, e.g. 12.34")
    if not dec.is_finite():
        raise ValidationError(f"{field} must be a finite number")
    if dec != dec.quantize(Decimal("0.01")):
        raise ValidationError(f"{field} cannot have more than 2 decimal places")
    cents = int(dec * 100)
    if cents <= 0:
        raise ValidationError(f"{field} must be greater than 0")
    if cents > MAX_AMOUNT_CENTS:
        raise ValidationError(f"{field} cannot exceed ${MAX_AMOUNT_CENTS // 100:,}")
    return cents


def fmt(cents: int) -> str:
    """Integer-only formatting (no float division): 1234 -> "12.34"."""
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}{dollars}.{rem:02d}"


def fmt_usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rem:02d}"

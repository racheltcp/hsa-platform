"""Money is integer cents everywhere. This module is the only place dollars are parsed."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .errors import ValidationError

MAX_AMOUNT_CENTS = 100_000_00   # sanity ceiling for one deposit or purchase
MAX_DOLLARS = Decimal(MAX_AMOUNT_CENTS) / 100


def parse_amount(value, field: str = "amount") -> int:
    """Turn a dollar amount ("12.34", 12.34 or 12) into positive integer cents.

    Goes through str -> Decimal so float noise never leaks in, and rejects
    fractions of a cent instead of silently rounding them.
    """
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        raise ValidationError("Enter an amount, like 12.34.", field=field)
    if len(str(value)) > 32:
        raise ValidationError("Enter an amount, like 12.34.", field=field)
    try:
        dollars = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValidationError("Enter an amount, like 12.34.", field=field)
    if not dollars.is_finite():
        raise ValidationError("Enter an amount, like 12.34.", field=field)
    # Bounds before any arithmetic: comparing works for any exponent, but
    # multiplying or rounding a value like 1e999999999 overflows.
    if dollars <= 0:
        raise ValidationError("Amount must be more than $0.", field=field)
    if dollars > MAX_DOLLARS:
        raise ValidationError(f"Amount can't be more than {fmt_usd(MAX_AMOUNT_CENTS)}.", field=field)
    # Safe now that 0 < dollars <= MAX_DOLLARS. (normalize() would underflow a
    # value like 1e-999999999 to zero and let it through.)
    if dollars != dollars.quantize(Decimal("0.01")):
        raise ValidationError("Amount can't include fractions of a cent.", field=field)
    return int(dollars * 100)


def fmt_usd(cents: int) -> str:
    """Integer-only formatting, no float division: 440000 -> "$4,400.00"."""
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rem:02d}"

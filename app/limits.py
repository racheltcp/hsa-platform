"""IRS annual HSA contribution limits.

Figures are the IRS's published inflation-adjusted limits for 2025 and 2026.
The age-55+ catch-up contribution is fixed by statute at $1,000. In a real
system these would live in a config table maintained by compliance, not code.
"""
from __future__ import annotations

from datetime import date

ANNUAL_LIMIT_CENTS = {
    2025: {"self_only": 4_300_00, "family": 8_550_00},
    2026: {"self_only": 4_400_00, "family": 8_750_00},
}
CATCH_UP_CENTS = 1_000_00
CATCH_UP_AGE = 55


def annual_limit_cents(coverage_type: str, date_of_birth: date, tax_year: int) -> int:
    # Fall back to the most recent known year if asked about a future year.
    year = tax_year if tax_year in ANNUAL_LIMIT_CENTS else max(ANNUAL_LIMIT_CENTS)
    limit = ANNUAL_LIMIT_CENTS[year][coverage_type]
    # Eligible for catch-up if the holder turns 55 at any point during the tax year.
    if tax_year - date_of_birth.year >= CATCH_UP_AGE:
        limit += CATCH_UP_CENTS
    return limit

"""Qualified-medical-expense decisions, based on the merchant category code (MCC).

Card networks tag every authorisation with a 4-digit ISO 18245 MCC describing
the merchant's line of business. Real HSA card programs use the MCC as the
first-pass eligibility filter, so this system does too.

Policy is an explicit ALLOW-list: any MCC not listed as qualified is declined.
An unknown or new category therefore fails closed, which is the safe default
for a tax-advantaged account (a wrongly approved purchase becomes taxable
income plus a 20% penalty for the account holder).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MerchantCategory:
    mcc: str
    label: str
    qualified: bool


_CATEGORIES = [
    # --- Qualified medical (allow-list) ---
    MerchantCategory("5912", "Drug stores & pharmacies", True),
    MerchantCategory("8062", "Hospitals", True),
    MerchantCategory("8011", "Doctors & physicians", True),
    MerchantCategory("8021", "Dentists & orthodontists", True),
    MerchantCategory("8031", "Osteopaths", True),
    MerchantCategory("8041", "Chiropractors", True),
    MerchantCategory("8042", "Optometrists & ophthalmologists", True),
    MerchantCategory("8043", "Opticians & eyeglasses", True),
    MerchantCategory("8049", "Podiatrists & chiropodists", True),
    MerchantCategory("8050", "Nursing & personal care facilities", True),
    MerchantCategory("8071", "Medical & dental laboratories", True),
    MerchantCategory("8099", "Medical services & health practitioners", True),
    MerchantCategory("4119", "Ambulance services", True),
    MerchantCategory("5047", "Medical, dental & hospital equipment", True),
    MerchantCategory("5975", "Hearing aids", True),
    MerchantCategory("5976", "Orthopedic goods & prosthetics", True),
    # --- Common non-qualified categories (listed for the UI; still declined) ---
    MerchantCategory("5812", "Restaurants", False),
    MerchantCategory("5814", "Fast food", False),
    MerchantCategory("5732", "Electronics stores", False),
    MerchantCategory("5411", "Grocery stores & supermarkets", False),
    MerchantCategory("5541", "Gas stations", False),
    MerchantCategory("5311", "Department stores", False),
    MerchantCategory("7997", "Gyms & membership clubs", False),
]

CATEGORIES: dict[str, MerchantCategory] = {c.mcc: c for c in _CATEGORIES}


def is_qualified(mcc: str) -> bool:
    category = CATEGORIES.get(mcc)
    return bool(category and category.qualified)


def describe(mcc: str) -> str:
    category = CATEGORIES.get(mcc)
    return category.label if category else f"Unknown category ({mcc})"

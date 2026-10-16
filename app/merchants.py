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
    group: str
    qualified: bool


_CATEGORIES = [
    # --- Qualified medical (allow-list) ---
    MerchantCategory("5912", "Drug stores & pharmacies", "Pharmacy", True),
    MerchantCategory("8062", "Hospitals", "Hospital", True),
    MerchantCategory("8011", "Doctors & physicians", "Medical provider", True),
    MerchantCategory("8021", "Dentists & orthodontists", "Dental", True),
    MerchantCategory("8031", "Osteopaths", "Medical provider", True),
    MerchantCategory("8041", "Chiropractors", "Medical provider", True),
    MerchantCategory("8042", "Optometrists & ophthalmologists", "Vision", True),
    MerchantCategory("8043", "Opticians & eyeglasses", "Vision", True),
    MerchantCategory("8049", "Podiatrists & chiropodists", "Medical provider", True),
    MerchantCategory("8050", "Nursing & personal care facilities", "Care facility", True),
    MerchantCategory("8071", "Medical & dental laboratories", "Lab", True),
    MerchantCategory("8099", "Medical services & health practitioners", "Medical provider", True),
    MerchantCategory("4119", "Ambulance services", "Emergency", True),
    MerchantCategory("5047", "Medical, dental & hospital equipment", "Medical supplies", True),
    MerchantCategory("5975", "Hearing aids", "Medical supplies", True),
    MerchantCategory("5976", "Orthopedic goods & prosthetics", "Medical supplies", True),
    # --- Common non-qualified categories (listed for the UI; still declined) ---
    MerchantCategory("5812", "Restaurants", "Restaurant", False),
    MerchantCategory("5814", "Fast food", "Restaurant", False),
    MerchantCategory("5732", "Electronics stores", "Electronics", False),
    MerchantCategory("5411", "Grocery stores & supermarkets", "Grocery", False),
    MerchantCategory("5541", "Gas stations", "Fuel", False),
    MerchantCategory("5311", "Department stores", "Retail", False),
    MerchantCategory("7997", "Gyms & membership clubs", "Fitness", False),
]

CATEGORIES: dict[str, MerchantCategory] = {c.mcc: c for c in _CATEGORIES}


def is_qualified(mcc: str) -> bool:
    category = CATEGORIES.get(mcc)
    return bool(category and category.qualified)


def describe(mcc: str) -> str:
    category = CATEGORIES.get(mcc)
    return category.label if category else f"Unknown category ({mcc})"

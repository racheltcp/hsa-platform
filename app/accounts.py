"""Accounts, deposits, and the account activity feed."""
from __future__ import annotations

import re
import sqlite3
from datetime import date

from . import common, limits
from .cards import card_to_dict
from .db import write_transaction
from .errors import Conflict, NotFound, ValidationError
from .money import fmt_usd, parse_amount
from .purchases import transaction_to_dict

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def create_account(conn: sqlite3.Connection, data: dict) -> dict:
    holder_name = common.require_text(data, "holder_name", "Full name")
    email = common.require_text(data, "email", "Email", max_len=254).lower()
    if not EMAIL_RE.match(email):
        raise ValidationError("Enter a valid email address, like jordan@example.com.", field="email")
    try:
        dob = date.fromisoformat(common.require_text(data, "date_of_birth", "Date of birth"))
    except ValueError:
        raise ValidationError("Date of birth must be a date like 1990-04-12.", field="date_of_birth")
    if not date(1900, 1, 1) <= dob < common.clock().date():
        raise ValidationError("Date of birth must be in the past.", field="date_of_birth")
    coverage_type = data.get("coverage_type")
    if coverage_type not in ("self_only", "family"):
        raise ValidationError("Coverage must be 'self_only' or 'family'.", field="coverage_type")

    account_id = common.new_id("acct")
    try:
        with write_transaction(conn):
            conn.execute(
                "INSERT INTO accounts (id, holder_name, email, date_of_birth, coverage_type, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (account_id, holder_name, email, dob.isoformat(), coverage_type, common.now_iso()))
    except sqlite3.IntegrityError:
        # The UNIQUE constraint, not a prior SELECT, decides: two simultaneous
        # sign-ups with the same email can't both get through.
        raise Conflict(f"An account with {email} already exists.", code="email_taken", field="email")
    return get_account(conn, account_id)


def get_account(conn: sqlite3.Connection, account_id: str) -> dict:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise NotFound(f"There is no account {account_id}.")
    return _account_to_dict(conn, row)


def list_accounts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
    return [_account_to_dict(conn, row) for row in rows]


def list_activity(conn: sqlite3.Connection, account_id: str, limit: int = 100) -> list[dict]:
    """Deposits and purchase attempts, newest first."""
    get_account(conn, account_id)
    # rowid breaks ties inside one millisecond. Writes are serialised, so
    # insertion order is commit order.
    deposits = conn.execute(
        "SELECT rowid AS seq, * FROM deposits WHERE account_id = ? ORDER BY rowid DESC LIMIT ?",
        (account_id, limit)).fetchall()
    purchases = conn.execute(
        "SELECT rowid AS seq, * FROM transactions WHERE account_id = ? ORDER BY rowid DESC LIMIT ?",
        (account_id, limit)).fetchall()
    items = [(r["created_at"], r["seq"], {"kind": "deposit", **_deposit_to_dict(r)}) for r in deposits]
    items += [(r["created_at"], r["seq"], {"kind": "purchase", **transaction_to_dict(r)}) for r in purchases]
    items.sort(key=lambda item: item[:2], reverse=True)
    return [item for _, _, item in items[:limit]]


def deposit(conn: sqlite3.Connection, account_id: str, data: dict) -> tuple[dict, bool]:
    """Add money, within the IRS annual limit. Returns (deposit, replayed).

    The year-to-date total is summed and the new deposit inserted inside one
    locked transaction. Without the lock, two deposits could each see room
    under the limit and both be accepted (scripts/mutation_check.py shows this).
    """
    amount_cents = parse_amount(data.get("amount"))
    key = common.idempotency_key(data)

    with write_transaction(conn):
        account = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if account is None:
            raise NotFound(f"There is no account {account_id}.")

        earlier = conn.execute("SELECT * FROM deposits WHERE account_id = ? AND idempotency_key = ?",
                               (account_id, key)).fetchone()
        if earlier:
            if earlier["amount_cents"] != amount_cents:
                raise Conflict("This idempotency key was already used for a different deposit.",
                               code="idempotency_key_reused", field="idempotency_key")
            return _deposit_to_dict(earlier), True

        tax_year = common.clock().year
        limit = limits.annual_limit_cents(
            account["coverage_type"], date.fromisoformat(account["date_of_birth"]), tax_year)
        contributed = _contributed_cents(conn, account_id, tax_year)
        if contributed + amount_cents > limit:
            raise Conflict(
                f"That would go over the {tax_year} IRS limit of {fmt_usd(limit)}. "
                f"You can deposit up to {fmt_usd(max(limit - contributed, 0))} more this year.",
                code="contribution_limit_exceeded", field="amount")

        balance = conn.execute(
            "UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ? RETURNING balance_cents",
            (amount_cents, account_id)).fetchall()[0][0]
        deposit_id = common.new_id("dep")
        conn.execute(
            "INSERT INTO deposits (id, account_id, amount_cents, tax_year, idempotency_key, "
            "balance_after_cents, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (deposit_id, account_id, amount_cents, tax_year, key, balance, common.now_iso()))
        row = conn.execute("SELECT * FROM deposits WHERE id = ?", (deposit_id,)).fetchone()
    return _deposit_to_dict(row), False


def _contributed_cents(conn: sqlite3.Connection, account_id: str, tax_year: int) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM deposits WHERE account_id = ? AND tax_year = ?",
        (account_id, tax_year)).fetchone()[0]


def _account_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    tax_year = common.clock().year
    limit = limits.annual_limit_cents(row["coverage_type"], date.fromisoformat(row["date_of_birth"]), tax_year)
    contributed = _contributed_cents(conn, row["id"], tax_year)
    card = conn.execute("SELECT * FROM cards WHERE account_id = ? AND status IN ('active', 'frozen')",
                        (row["id"],)).fetchone()
    return {
        "id": row["id"],
        "holder_name": row["holder_name"],
        "email": row["email"],
        "date_of_birth": row["date_of_birth"],
        "coverage_type": row["coverage_type"],
        "balance_cents": row["balance_cents"],
        "contributions": {
            "tax_year": tax_year,
            "contributed_cents": contributed,
            "limit_cents": limit,
            "remaining_cents": max(limit - contributed, 0),
        },
        "card": card_to_dict(card) if card else None,
        "created_at": row["created_at"],
    }


def _deposit_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "amount_cents": row["amount_cents"],
        "tax_year": row["tax_year"],
        "balance_after_cents": row["balance_after_cents"],
        "idempotency_key": row["idempotency_key"],
        "created_at": row["created_at"],
    }

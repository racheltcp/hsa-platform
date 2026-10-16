"""Business logic. Framework-free: every function takes a sqlite3 connection, so
the same code is exercised by the HTTP API and directly by the tests.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
import time
from datetime import date, datetime, timezone
from typing import Callable

from . import limits, merchants
from .db import write_transaction
from .errors import Conflict, NotFound, ValidationError
from .money import fmt, fmt_usd, parse_amount

# Injectable clock so tests can simulate card expiry and tax-year rollover.
clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


class DeclineReason:
    CARD_FROZEN = "CARD_FROZEN"
    CARD_CANCELLED = "CARD_CANCELLED"
    CARD_EXPIRED = "CARD_EXPIRED"
    ACCOUNT_CLOSED = "ACCOUNT_CLOSED"
    NOT_QUALIFIED_MEDICAL_EXPENSE = "NOT_QUALIFIED_MEDICAL_EXPENSE"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"


DECLINE_MESSAGES = {
    DeclineReason.CARD_FROZEN: "Card is frozen",
    DeclineReason.CARD_CANCELLED: "Card has been cancelled",
    DeclineReason.CARD_EXPIRED: "Card is expired",
    DeclineReason.ACCOUNT_CLOSED: "Account is closed",
    DeclineReason.NOT_QUALIFIED_MEDICAL_EXPENSE: "Merchant is not a qualified medical expense",
    DeclineReason.INSUFFICIENT_FUNDS: "Insufficient funds",
}

CARD_VALIDITY_YEARS = 3
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --------------------------------------------------------------------------- helpers

def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _now_iso() -> str:
    return clock().isoformat(timespec="milliseconds")


def _require_str(data: dict, field: str, max_len: int = 200) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} is required")
    value = value.strip()
    if len(value) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters")
    return value


def _optional_key(data: dict) -> str:
    key = data.get("idempotency_key")
    if key is None or key == "":
        return secrets.token_hex(16)
    if not isinstance(key, str) or len(key) > 100:
        raise ValidationError("idempotency_key must be a string of at most 100 characters")
    return key


def _luhn_check_digit(partial: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(partial)):
        d = int(ch)
        if i % 2 == 0:  # these positions are doubled once the check digit is appended
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def _generate_pan() -> str:
    # 4xxxxx = Visa-style BIN reserved for this simulation. Luhn-valid.
    partial = "493721" + "".join(str(secrets.randbelow(10)) for _ in range(9))
    return partial + _luhn_check_digit(partial)


def _get_account_row(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise NotFound(f"Account {account_id} not found")
    return row


def _contributions_cents(conn, account_id: str, tax_year: int) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM deposits WHERE account_id = ? AND tax_year = ?",
        (account_id, tax_year),
    ).fetchone()[0]


# --------------------------------------------------------------------------- serializers

def card_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "last4": row["last4"],
        "masked_number": f"•••• •••• •••• {row['last4']}",
        "exp_month": row["exp_month"],
        "exp_year": row["exp_year"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def transaction_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "account_id": row["account_id"],
        "card_id": row["card_id"],
        "merchant_name": row["merchant_name"],
        "mcc": row["mcc"],
        "merchant_category": merchants.describe(row["mcc"]),
        "amount": fmt(row["amount_cents"]),
        "amount_cents": row["amount_cents"],
        "status": row["status"],
        "decline_reason": row["decline_reason"],
        "decline_message": DECLINE_MESSAGES.get(row["decline_reason"]),
        "balance_after": fmt(row["balance_after_cents"]),
        "balance_after_cents": row["balance_after_cents"],
        "decision": decision_trace(row),
        "created_at": row["created_at"],
    }


# Authorization checks, in the order process_transaction evaluates them.
# (key, what the check asserts, decline reasons that mean this check failed)
AUTH_CHECKS = [
    ("card_active", "Card is active", (DeclineReason.CARD_FROZEN, DeclineReason.CARD_CANCELLED)),
    ("card_not_expired", "Card is not expired", (DeclineReason.CARD_EXPIRED,)),
    ("account_open", "Account is open", (DeclineReason.ACCOUNT_CLOSED,)),
    ("qualified_merchant", "Merchant is a qualified medical expense",
     (DeclineReason.NOT_QUALIFIED_MEDICAL_EXPENSE,)),
    ("sufficient_funds", "Balance covers the amount", (DeclineReason.INSUFFICIENT_FUNDS,)),
]


def decision_trace(row: sqlite3.Row) -> list[dict]:
    """Reconstruct the ordered list of checks for a stored transaction.

    Checks run in a fixed order and stop at the first failure, so the stored
    decline_reason fully determines the trace: everything before the failing
    check passed, everything after it was never evaluated. No extra columns needed.
    """
    reason = row["decline_reason"]
    category = f"{merchants.describe(row['mcc'])} (MCC {row['mcc']})"
    amount, balance = fmt_usd(row["amount_cents"]), fmt_usd(row["balance_after_cents"])
    details = {
        "card_active": {"passed": "Card was active",
                        "failed": DECLINE_MESSAGES.get(reason, "")},
        "card_not_expired": {"passed": "Card was within its expiry date",
                             "failed": "Card was past its expiry date"},
        "account_open": {"passed": "Account was open", "failed": "Account was closed"},
        "qualified_merchant": {"passed": f"{category} is on the medical allow-list",
                               "failed": f"{category} is not on the medical allow-list"},
        "sufficient_funds": {"passed": f"Debited {amount}, leaving {balance}",
                             "failed": f"Needed {amount}, but the balance was {balance}"},
    }
    trace, failed_already = [], False
    for key, label, reasons in AUTH_CHECKS:
        if failed_already:
            status, detail = "skipped", "Not checked because an earlier check failed"
        elif reason in reasons:
            status, detail, failed_already = "failed", details[key]["failed"], True
        else:
            status, detail = "passed", details[key]["passed"]
        trace.append({"check": key, "label": label, "status": status, "detail": detail})
    return trace


def deposit_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "account_id": row["account_id"],
        "amount": fmt(row["amount_cents"]),
        "amount_cents": row["amount_cents"],
        "tax_year": row["tax_year"],
        "balance_after": fmt(row["balance_after_cents"]),
        "balance_after_cents": row["balance_after_cents"],
        "created_at": row["created_at"],
    }


def account_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    tax_year = clock().year
    contributed = _contributions_cents(conn, row["id"], tax_year)
    limit = limits.annual_limit_cents(
        row["coverage_type"], date.fromisoformat(row["date_of_birth"]), tax_year
    )
    card = conn.execute(
        "SELECT * FROM cards WHERE account_id = ? AND status IN ('active', 'frozen')",
        (row["id"],),
    ).fetchone()
    return {
        "id": row["id"],
        "holder_name": row["holder_name"],
        "email": row["email"],
        "date_of_birth": row["date_of_birth"],
        "coverage_type": row["coverage_type"],
        "status": row["status"],
        "balance": fmt(row["balance_cents"]),
        "balance_cents": row["balance_cents"],
        "contributions": {
            "tax_year": tax_year,
            "contributed": fmt(contributed),
            "contributed_cents": contributed,
            "annual_limit": fmt(limit),
            "annual_limit_cents": limit,
            "remaining": fmt(max(limit - contributed, 0)),
            "remaining_cents": max(limit - contributed, 0),
        },
        "card": card_to_dict(card) if card else None,
        "created_at": row["created_at"],
    }


# --------------------------------------------------------------------------- accounts

def create_account(conn: sqlite3.Connection, data: dict) -> dict:
    holder_name = _require_str(data, "holder_name", 100)
    email = _require_str(data, "email", 254).lower()
    if not EMAIL_RE.match(email):
        raise ValidationError("email is not a valid email address")
    dob_raw = _require_str(data, "date_of_birth", 10)
    try:
        dob = date.fromisoformat(dob_raw)
    except ValueError:
        raise ValidationError("date_of_birth must be an ISO date (YYYY-MM-DD)")
    today = clock().date()
    if dob >= today or dob.year < 1900:
        raise ValidationError("date_of_birth must be a past date")
    coverage_type = data.get("coverage_type")
    if coverage_type not in ("self_only", "family"):
        raise ValidationError("coverage_type must be 'self_only' or 'family'")

    account_id = _new_id("acct")
    try:
        with write_transaction(conn):
            conn.execute(
                """INSERT INTO accounts (id, holder_name, email, date_of_birth, coverage_type, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (account_id, holder_name, email, dob.isoformat(), coverage_type, _now_iso()),
            )
    except sqlite3.IntegrityError:
        raise Conflict(f"An account with email {email} already exists", code="email_taken")
    return get_account(conn, account_id)


def get_account(conn: sqlite3.Connection, account_id: str) -> dict:
    return account_to_dict(conn, _get_account_row(conn, account_id))


def list_accounts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
    return [account_to_dict(conn, r) for r in rows]


def list_activity(conn: sqlite3.Connection, account_id: str, limit: int = 100) -> list[dict]:
    _get_account_row(conn, account_id)
    # rowid breaks ties within the same millisecond: writes are serialised, so
    # insertion order is commit order.
    deposits = conn.execute(
        "SELECT rowid AS seq, * FROM deposits WHERE account_id = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?", (account_id, limit),
    ).fetchall()
    txns = conn.execute(
        "SELECT rowid AS seq, * FROM transactions WHERE account_id = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?", (account_id, limit),
    ).fetchall()
    items = [({"kind": "deposit", **deposit_to_dict(d)}, d["seq"]) for d in deposits]
    items += [({"kind": "purchase", **transaction_to_dict(t)}, t["seq"]) for t in txns]
    items.sort(key=lambda pair: (pair[0]["created_at"], pair[1]), reverse=True)
    return [item for item, _ in items[:limit]]


# --------------------------------------------------------------------------- deposits

def deposit(conn: sqlite3.Connection, account_id: str, data: dict) -> tuple[dict, bool]:
    """Add funds. Returns (deposit, replayed).

    Enforces the IRS annual contribution limit. The year-to-date sum and the
    insert happen inside one BEGIN IMMEDIATE transaction, so two concurrent
    deposits cannot both squeeze under the limit.
    """
    amount_cents = parse_amount(data.get("amount"))
    key = _optional_key(data)

    with write_transaction(conn):
        existing = conn.execute(
            "SELECT * FROM deposits WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if existing:
            if existing["account_id"] != account_id or existing["amount_cents"] != amount_cents:
                raise Conflict("idempotency_key was already used for a different deposit",
                               code="idempotency_key_reused")
            return deposit_to_dict(existing), True

        account = _get_account_row(conn, account_id)
        if account["status"] != "active":
            raise Conflict("Account is closed", code="account_closed")

        tax_year = clock().year
        contributed = _contributions_cents(conn, account_id, tax_year)
        limit = limits.annual_limit_cents(
            account["coverage_type"], date.fromisoformat(account["date_of_birth"]), tax_year
        )
        if contributed + amount_cents > limit:
            raise Conflict(
                f"Deposit would exceed the {tax_year} contribution limit of {fmt_usd(limit)} "
                f"({fmt_usd(max(limit - contributed, 0))} remaining)",
                code="contribution_limit_exceeded",
            )

        new_balance = conn.execute(
            "UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ? RETURNING balance_cents",
            (amount_cents, account_id),
        ).fetchall()[0][0]
        deposit_id = _new_id("dep")
        conn.execute(
            """INSERT INTO deposits (id, account_id, amount_cents, tax_year, idempotency_key,
                                     balance_after_cents, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (deposit_id, account_id, amount_cents, tax_year, key, new_balance, _now_iso()),
        )
        row = conn.execute("SELECT * FROM deposits WHERE id = ?", (deposit_id,)).fetchone()
    return deposit_to_dict(row), False


# --------------------------------------------------------------------------- cards

def issue_card(conn: sqlite3.Connection, account_id: str, data: dict | None = None) -> dict:
    """Issue a virtual debit card. With replace=true, cancels the current card first.

    The full card number is returned exactly once, in this response, and never
    stored: the database keeps only the last 4 digits.
    """
    replace = bool((data or {}).get("replace"))
    pan = _generate_pan()
    now = clock()
    card_id = _new_id("card")

    with write_transaction(conn):
        account = _get_account_row(conn, account_id)
        if account["status"] != "active":
            raise Conflict("Account is closed", code="account_closed")
        live = conn.execute(
            "SELECT id FROM cards WHERE account_id = ? AND status IN ('active', 'frozen')",
            (account_id,),
        ).fetchone()
        if live and not replace:
            raise Conflict(
                "Account already has a card. Pass replace=true to cancel it and issue a new one.",
                code="card_exists",
            )
        if live:
            conn.execute("UPDATE cards SET status = 'cancelled' WHERE id = ?", (live["id"],))
        conn.execute(
            """INSERT INTO cards (id, account_id, last4, exp_month, exp_year, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?)""",
            (card_id, account_id, pan[-4:], now.month, now.year + CARD_VALIDITY_YEARS, _now_iso()),
        )
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    card = card_to_dict(row)
    card["number"] = " ".join(pan[i:i + 4] for i in range(0, 16, 4))
    return card


def set_card_status(conn: sqlite3.Connection, card_id: str, status: str) -> dict:
    allowed = {"frozen": ("active",), "active": ("frozen",)}
    with write_transaction(conn):
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            raise NotFound(f"Card {card_id} not found")
        if row["status"] not in allowed[status]:
            raise Conflict(f"Cannot change a {row['status']} card to {status}",
                           code="invalid_card_state")
        conn.execute("UPDATE cards SET status = ? WHERE id = ?", (status, card_id))
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return card_to_dict(row)


# --------------------------------------------------------------------------- transactions

def process_transaction(conn: sqlite3.Connection, data: dict) -> tuple[dict, bool]:
    """Authorise a card purchase. Returns (transaction, replayed).

    Every attempt, approved or declined, is recorded. Checks run in this order:
    card state -> account state -> merchant eligibility -> funds.

    Concurrency (see architecture.md, and scripts/mutation_check.py for proof):
    1. BEGIN IMMEDIATE serialises the whole decision. This is the load-bearing
       guard: it is what makes the idempotency lookup + insert atomic.
    2. The debit is a single conditional UPDATE (... WHERE balance_cents >= ?),
       so "check funds" and "take funds" are one statement. On its own this
       protects the balance, but NOT idempotency or deposit limits.
    3. CHECK (balance_cents >= 0) in the schema turns any remaining overdraft bug
       into a failed write instead of a negative balance. It cannot catch lost
       updates that leave the balance positive.
    """
    card_id = _require_str(data, "card_id", 64)
    merchant_name = _require_str(data, "merchant_name", 100)
    mcc = _require_str(data, "mcc", 4)
    if not re.fullmatch(r"\d{4}", mcc):
        raise ValidationError("mcc must be a 4-digit merchant category code")
    amount_cents = parse_amount(data.get("amount"))
    key = _optional_key(data)

    received_at = time.time()
    with write_transaction(conn):
        lock_acquired_at = time.time()          # BEGIN IMMEDIATE has returned: we hold the lock
        existing = conn.execute(
            "SELECT * FROM transactions WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if existing:
            same = (existing["card_id"], existing["amount_cents"], existing["mcc"]) == (
                card_id, amount_cents, mcc)
            if not same:
                raise Conflict("idempotency_key was already used for a different transaction",
                               code="idempotency_key_reused")
            return transaction_to_dict(existing), True

        card = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if card is None:
            raise NotFound(f"Card {card_id} not found")
        account = _get_account_row(conn, card["account_id"])

        reason = _decline_reason(card, account, mcc)
        if reason is None:
            debited = conn.execute(
                """UPDATE accounts SET balance_cents = balance_cents - ?
                   WHERE id = ? AND balance_cents >= ?
                   RETURNING balance_cents""",
                (amount_cents, account["id"], amount_cents),
            ).fetchall()
            if not debited:
                reason = DeclineReason.INSUFFICIENT_FUNDS
                balance_after = account["balance_cents"]
            else:
                balance_after = debited[0][0]
        else:
            balance_after = account["balance_cents"]

        txn_id = _new_id("txn")
        conn.execute(
            """INSERT INTO transactions (id, idempotency_key, account_id, card_id, merchant_name, mcc,
                                         amount_cents, status, decline_reason, balance_after_cents,
                                         created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (txn_id, key, account["id"], card_id, merchant_name, mcc, amount_cents,
             "approved" if reason is None else "declined", reason, balance_after, _now_iso()),
        )
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        decided_at = time.time()                # last statement done; COMMIT comes next
    committed_at = time.time()                  # COMMIT has returned: lock released

    txn = transaction_to_dict(row)
    # Diagnostics for this request only (not persisted): how long it queued for
    # the write lock and how long it held it. Epoch milliseconds.
    txn["processing"] = {
        "received_at_ms": received_at * 1000,
        "lock_acquired_at_ms": lock_acquired_at * 1000,
        "decided_at_ms": decided_at * 1000,
        "committed_at_ms": committed_at * 1000,
        "lock_wait_ms": round((lock_acquired_at - received_at) * 1000, 3),
        "lock_held_ms": round((committed_at - lock_acquired_at) * 1000, 3),
    }
    return txn, False


def _decline_reason(card: sqlite3.Row, account: sqlite3.Row, mcc: str) -> str | None:
    if card["status"] == "frozen":
        return DeclineReason.CARD_FROZEN
    if card["status"] == "cancelled":
        return DeclineReason.CARD_CANCELLED
    now = clock()
    if (now.year, now.month) > (card["exp_year"], card["exp_month"]):
        return DeclineReason.CARD_EXPIRED
    if account["status"] != "active":
        return DeclineReason.ACCOUNT_CLOSED
    if not merchants.is_qualified(mcc):
        return DeclineReason.NOT_QUALIFIED_MEDICAL_EXPENSE
    return None

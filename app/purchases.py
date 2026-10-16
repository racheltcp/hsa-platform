"""Card purchase authorization: the part of the system that has to be right.

Every purchase runs the CHECKS below in order and stops at the first one that
fails. That list is the single source of truth: authorize_purchase uses it to
decide, and decision_trace uses it to explain a stored decision later.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from . import common, merchants
from .db import write_transaction
from .errors import Conflict, NotFound, ValidationError
from .money import fmt_usd, parse_amount


@dataclass(frozen=True)
class Check:
    name: str
    label: str              # what has to be true, as shown to people
    decline_reason: str     # stored on the transaction when this check fails


CHECKS = [
    Check("card_not_cancelled", "Card has not been cancelled", "CARD_CANCELLED"),
    Check("card_not_frozen", "Card is not frozen", "CARD_FROZEN"),
    Check("card_not_expired", "Card is not expired", "CARD_EXPIRED"),
    Check("merchant_qualified", "Merchant is a qualified medical expense", "NOT_QUALIFIED_MEDICAL_EXPENSE"),
    Check("sufficient_funds", "Balance covers the amount", "INSUFFICIENT_FUNDS"),
]
FUNDS_CHECK = CHECKS[-1]


def authorize_purchase(conn: sqlite3.Connection, data: dict) -> tuple[dict, bool]:
    """Approve or decline one card purchase. Returns (transaction, replayed).

    Every attempt is recorded, approved or declined. Concurrency safety rests on
    three layers (architecture.md has the measurements):
      1. write_transaction's BEGIN IMMEDIATE lets one purchase decide at a time.
         This is the layer everything depends on; it also makes the idempotency
         lookup-then-insert atomic.
      2. The debit is a conditional UPDATE, so checking and taking the money is
         one statement.
      3. CHECK (balance_cents >= 0) in the schema rejects any write that would
         overdraw, whatever the application code does.
    """
    request = _parse_request(data)

    lock_requested = time.perf_counter()
    with write_transaction(conn):
        lock_acquired = time.perf_counter()

        earlier = conn.execute(
            "SELECT * FROM transactions WHERE card_id = ? AND idempotency_key = ?",
            (request["card_id"], request["idempotency_key"])).fetchone()
        if earlier:
            return _replay(earlier, request), True

        card = conn.execute("SELECT * FROM cards WHERE id = ?", (request["card_id"],)).fetchone()
        if card is None:
            raise NotFound(f"There is no card {request['card_id']}.", field="card_id")
        balance = conn.execute("SELECT balance_cents FROM accounts WHERE id = ?",
                               (card["account_id"],)).fetchone()[0]

        reason = _first_failed_rule(card, request["mcc"])
        if reason is None:
            # Only takes the money if it's there. No row back means it wasn't.
            debited = conn.execute(
                "UPDATE accounts SET balance_cents = balance_cents - :amount "
                "WHERE id = :account AND balance_cents >= :amount RETURNING balance_cents",
                {"amount": request["amount_cents"], "account": card["account_id"]}).fetchall()
            if debited:
                balance = debited[0][0]
            else:
                reason = FUNDS_CHECK.decline_reason

        txn_id = common.new_id("txn")
        conn.execute(
            "INSERT INTO transactions (id, idempotency_key, account_id, card_id, merchant_name, mcc, "
            "amount_cents, status, decline_reason, balance_after_cents, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (txn_id, request["idempotency_key"], card["account_id"], card["id"], request["merchant_name"],
             request["mcc"], request["amount_cents"], "declined" if reason else "approved", reason,
             balance, common.now_iso()))
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        decided = time.perf_counter()
    released = time.perf_counter()

    txn = transaction_to_dict(row)
    # How long this request queued for the lock and held it. perf_counter is a
    # monotonic clock, so values compare across requests in this server process
    # but mean nothing as wall-clock times. Not stored.
    txn["lock_timing"] = {
        "requested_ms": lock_requested * 1000,
        "acquired_ms": lock_acquired * 1000,
        "decided_ms": decided * 1000,
        "released_ms": released * 1000,
    }
    return txn, False


def _first_failed_rule(card: sqlite3.Row, mcc: str) -> str | None:
    """Run every check except funds, in CHECKS order. Funds is decided by the
    conditional UPDATE so that checking and debiting can't be separated."""
    today = common.clock()
    passes = {
        "card_not_cancelled": card["status"] != "cancelled",
        "card_not_frozen": card["status"] != "frozen",
        "card_not_expired": (today.year, today.month) <= (card["exp_year"], card["exp_month"]),
        "merchant_qualified": merchants.is_qualified(mcc),
    }
    for check in CHECKS:
        if check is not FUNDS_CHECK and not passes[check.name]:
            return check.decline_reason
    return None


def _parse_request(data: dict) -> dict:
    mcc = common.require_text(data, "mcc", "Merchant category", max_len=4)
    if not (len(mcc) == 4 and mcc.isdigit()):
        raise ValidationError("Merchant category must be a 4-digit code, like 5912.", field="mcc")
    return {
        "card_id": common.require_text(data, "card_id", "Card"),
        "merchant_name": common.require_text(data, "merchant_name", "Merchant name"),
        "mcc": mcc,
        "amount_cents": parse_amount(data.get("amount")),
        "idempotency_key": common.idempotency_key(data),
    }


def _replay(earlier: sqlite3.Row, request: dict) -> dict:
    """A retry must be the same purchase. Returning the stored result for a
    different one would silently report the wrong outcome."""
    same = (earlier["amount_cents"], earlier["mcc"], earlier["merchant_name"]) == (
        request["amount_cents"], request["mcc"], request["merchant_name"])
    if not same:
        raise Conflict("This idempotency key was already used for a different purchase on this card.",
                       code="idempotency_key_reused", field="idempotency_key")
    return transaction_to_dict(earlier)


def decision_trace(row: sqlite3.Row) -> list[dict]:
    """Rebuild the checks for a stored transaction from its decline_reason.

    Checks run in a fixed order and stop at the first failure, so the reason
    pins down the whole trace: earlier checks passed, later ones never ran.
    """
    failed_at = next((i for i, c in enumerate(CHECKS) if c.decline_reason == row["decline_reason"]),
                     len(CHECKS))
    category = f"{merchants.describe(row['mcc'])} (MCC {row['mcc']})"
    amount, balance = fmt_usd(row["amount_cents"]), fmt_usd(row["balance_after_cents"])
    details = {
        # name: (detail if passed, detail if failed)
        "card_not_cancelled": ("Card was in use", "Card had been cancelled and replaced"),
        "card_not_frozen": ("Card was not frozen", "Card was frozen by the account holder"),
        "card_not_expired": ("Card was within its expiry date", "Card was past its expiry date"),
        "merchant_qualified": (f"{category} is on the medical allow-list",
                               f"{category} is not on the medical allow-list"),
        "sufficient_funds": (f"Took {amount}, leaving {balance}",
                             f"Needed {amount}, but the balance was {balance}"),
    }
    trace = []
    for i, check in enumerate(CHECKS):
        if i < failed_at:
            status, detail = "passed", details[check.name][0]
        elif i == failed_at:
            status, detail = "failed", details[check.name][1]
        else:
            status, detail = "skipped", "Not checked, because an earlier check failed"
        trace.append({"check": check.name, "label": check.label, "status": status, "detail": detail})
    return trace


def transaction_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "card_id": row["card_id"],
        "merchant_name": row["merchant_name"],
        "mcc": row["mcc"],
        "merchant_category": merchants.describe(row["mcc"]),
        "amount_cents": row["amount_cents"],
        "status": row["status"],
        "decline_reason": row["decline_reason"],
        "balance_after_cents": row["balance_after_cents"],
        "decision": decision_trace(row),
        "idempotency_key": row["idempotency_key"],
        "created_at": row["created_at"],
    }

"""Virtual debit cards: issue, replace, freeze."""
from __future__ import annotations

import secrets
import sqlite3

from . import common
from .db import write_transaction
from .errors import Conflict, NotFound

CARD_VALIDITY_YEARS = 3
SIMULATION_BIN = "493721"   # first six digits of every card number this app issues


def issue_card(conn: sqlite3.Connection, account_id: str, replace: bool = False) -> dict:
    """Issue a card. With replace=True, cancel the account's current card first.

    The full card number appears only in this response. The database keeps the
    last four digits; purchases refer to the card by its id, the way a card
    network refers to a token rather than the raw number.
    """
    number = _generate_card_number()
    issued = common.clock()
    card_id = common.new_id("card")

    with write_transaction(conn):
        if conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone() is None:
            raise NotFound(f"There is no account {account_id}.")
        current = conn.execute(
            "SELECT id FROM cards WHERE account_id = ? AND status IN ('active', 'frozen')",
            (account_id,)).fetchone()
        if current and not replace:
            raise Conflict("This account already has a card. Replace it to get a new number.",
                           code="card_exists")
        if current:
            conn.execute("UPDATE cards SET status = 'cancelled' WHERE id = ?", (current["id"],))
        conn.execute(
            "INSERT INTO cards (id, account_id, last4, exp_month, exp_year, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (card_id, account_id, number[-4:], issued.month, issued.year + CARD_VALIDITY_YEARS,
             common.now_iso()))
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return {**card_to_dict(row), "number": " ".join(number[i:i + 4] for i in range(0, 16, 4))}


def set_frozen(conn: sqlite3.Connection, card_id: str, frozen: bool) -> dict:
    current, new = ("active", "frozen") if frozen else ("frozen", "active")
    with write_transaction(conn):
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            raise NotFound(f"There is no card {card_id}.")
        if row["status"] != current:
            raise Conflict(f"This card is {row['status']}, so it can't be {'frozen' if frozen else 'unfrozen'}.",
                           code="invalid_card_state")
        conn.execute("UPDATE cards SET status = ? WHERE id = ?", (new, card_id))
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return card_to_dict(row)


def card_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "last4": row["last4"],
        "exp_month": row["exp_month"],
        "exp_year": row["exp_year"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def _generate_card_number() -> str:
    body = SIMULATION_BIN + "".join(str(secrets.randbelow(10)) for _ in range(9))
    return body + _luhn_check_digit(body)


def _luhn_check_digit(body: str) -> str:
    """The digit that makes body + digit pass the Luhn checksum card networks use."""
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:          # every second digit from the right, once the check digit is added
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return str((10 - total % 10) % 10)

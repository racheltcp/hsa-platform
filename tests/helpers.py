from __future__ import annotations

import itertools
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from app import accounts, cards, common, purchases
from app.db import connect, init_db

_emails = itertools.count(1)


class DbTestCase(unittest.TestCase):
    """A fresh SQLite file per test. On disk rather than in memory, so several
    threads with their own connections share it exactly like the real server."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="hsa-test-")
        self.db_path = str(Path(self._tmp) / "test.db")
        init_db(self.db_path)
        self.conn = connect(self.db_path)
        self._real_clock = common.clock

    def tearDown(self):
        common.clock = self._real_clock
        self.conn.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def set_today(self, year, month, day):
        common.clock = lambda: datetime(year, month, day, 12, tzinfo=timezone.utc)

    def make_account(self, balance: str | None = None, coverage="self_only", dob="1990-05-01",
                     with_card=True):
        acct = accounts.create_account(self.conn, {
            "holder_name": "Test Holder", "email": f"holder{next(_emails)}@example.com",
            "date_of_birth": dob, "coverage_type": coverage})
        if balance:
            accounts.deposit(self.conn, acct["id"], {"amount": balance})
        card = cards.issue_card(self.conn, acct["id"]) if with_card else None
        return acct, card

    def buy(self, card, amount, mcc="5912", key=None, merchant="Test Merchant", conn=None):
        txn, _ = purchases.authorize_purchase(conn or self.conn, {
            "card_id": card["id"], "amount": amount, "merchant_name": merchant,
            "mcc": mcc, "idempotency_key": key})
        return txn

    def buy_on_own_connection(self, card_id, amount, mcc="5912", key=None):
        """What one HTTP request does: its own connection, then a purchase."""
        conn = connect(self.db_path)
        try:
            return self.buy({"id": card_id}, amount, mcc, key, conn=conn)
        finally:
            conn.close()

    def balance(self, account_id) -> int:
        return self.conn.execute("SELECT balance_cents FROM accounts WHERE id = ?",
                                 (account_id,)).fetchone()[0]

    def assert_ledger_consistent(self, account_id):
        """balance == all deposits - approved purchases, and never negative."""
        deposited = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM deposits WHERE account_id = ?",
            (account_id,)).fetchone()[0]
        spent = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM transactions "
            "WHERE account_id = ? AND status = 'approved'", (account_id,)).fetchone()[0]
        balance = self.balance(account_id)
        self.assertGreaterEqual(balance, 0)
        self.assertEqual(balance, deposited - spent,
                         f"ledger drift: balance={balance} deposited={deposited} spent={spent}")


def run_concurrently(fn, args_list):
    """Run fn(*args) on one thread per item, all released at the same instant."""
    barrier = threading.Barrier(len(args_list))

    def worker(args):
        barrier.wait()
        return fn(*args)

    with ThreadPoolExecutor(max_workers=len(args_list)) as pool:
        return list(pool.map(worker, args_list))

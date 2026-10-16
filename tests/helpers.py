import shutil
import tempfile
import unittest
from pathlib import Path

from app import services
from app.db import connect, init_db


class DbTestCase(unittest.TestCase):
    """Each test gets a fresh on-disk SQLite database (on disk, not :memory:,
    so multiple threads/connections can share it exactly like the real app)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="hsa-test-")
        self.db_path = str(Path(self._tmp) / "test.db")
        init_db(self.db_path)
        self.conn = connect(self.db_path)
        self._original_clock = services.clock

    def tearDown(self):
        services.clock = self._original_clock
        self.conn.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- fixtures -----------------------------------------------------------------
    _seq = 0

    def make_account(self, balance: str | None = None, coverage="self_only",
                     dob="1990-05-01", with_card=True):
        DbTestCase._seq += 1
        acct = services.create_account(self.conn, {
            "holder_name": "Test Holder",
            "email": f"holder{DbTestCase._seq}@example.com",
            "date_of_birth": dob,
            "coverage_type": coverage,
        })
        if balance:
            services.deposit(self.conn, acct["id"], {"amount": balance})
        card = services.issue_card(self.conn, acct["id"]) if with_card else None
        return acct, card

    def purchase(self, card, amount, mcc="5912", key=None, conn=None):
        txn, _ = services.process_transaction(conn or self.conn, {
            "card_id": card["id"], "amount": amount, "merchant_name": "Test Merchant",
            "mcc": mcc, "idempotency_key": key,
        })
        return txn

    def balance_cents(self, account_id):
        return self.conn.execute(
            "SELECT balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()[0]

    def assert_ledger_consistent(self, account_id):
        """balance == sum(deposits) - sum(approved purchases), and never negative."""
        deposits = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) FROM deposits WHERE account_id=?",
            (account_id,)).fetchone()[0]
        spent = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) FROM transactions "
            "WHERE account_id=? AND status='approved'", (account_id,)).fetchone()[0]
        balance = self.balance_cents(account_id)
        self.assertGreaterEqual(balance, 0)
        self.assertEqual(balance, deposits - spent,
                         f"ledger drift: balance={balance} deposits={deposits} spent={spent}")

"""Concurrency tests. These use real OS threads, each with its own SQLite
connection, released simultaneously by a barrier, which is the same shape as
concurrent HTTP requests hitting the threaded server.

The last test class is a control: it shows that a naive read-then-write
implementation DOES lose money under this exact harness. Without that control,
passing concurrency tests would prove nothing (a harness that never actually
interleaves would pass too).
"""
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from app import services
from app.db import connect
from app.errors import Conflict
from app.services import DeclineReason
from tests.helpers import DbTestCase


def run_concurrently(fn, args_list):
    """Run fn(*args) on one thread per item, all released at the same instant."""
    barrier = threading.Barrier(len(args_list))

    def worker(args):
        barrier.wait()
        return fn(*args)

    with ThreadPoolExecutor(max_workers=len(args_list)) as pool:
        return list(pool.map(worker, args_list))


class ConcurrentPurchaseTests(DbTestCase):
    def _purchase_on_own_connection(self, card_id, amount, mcc="5912", key=None):
        conn = connect(self.db_path)
        try:
            txn, _ = services.process_transaction(conn, {
                "card_id": card_id, "amount": amount, "merchant_name": "Race",
                "mcc": mcc, "idempotency_key": key})
            return txn
        finally:
            conn.close()

    def test_spec_example_100_balance_80_and_50_concurrently(self):
        """The exact scenario from the brief, repeated many times to shake out interleavings."""
        for round_no in range(50):
            with self.subTest(round=round_no):
                acct, card = self.make_account("100")
                results = run_concurrently(self._purchase_on_own_connection,
                                           [(card["id"], "80"), (card["id"], "50")])
                approved = [r for r in results if r["status"] == "approved"]
                declined = [r for r in results if r["status"] == "declined"]
                self.assertEqual(len(approved), 1, results)
                self.assertEqual(declined[0]["decline_reason"], DeclineReason.INSUFFICIENT_FUNDS)
                # Whichever arrived first wins; the balance must match the winner.
                self.assertEqual(self.balance_cents(acct["id"]), 10000 - approved[0]["amount_cents"])
                self.assert_ledger_consistent(acct["id"])

    def test_many_concurrent_purchases_never_overdraw(self):
        acct, card = self.make_account("100")
        results = run_concurrently(self._purchase_on_own_connection,
                                   [(card["id"], "10")] * 40)
        approved = sum(r["status"] == "approved" for r in results)
        self.assertEqual(approved, 10)  # exactly $100 worth, no more, no less
        self.assertEqual(self.balance_cents(acct["id"]), 0)
        self.assert_ledger_consistent(acct["id"])

    def test_mixed_valid_and_invalid_concurrent_purchases(self):
        acct, card = self.make_account("100")
        jobs = [(card["id"], "25", "5912")] * 8 + [(card["id"], "25", "5812")] * 8
        results = run_concurrently(self._purchase_on_own_connection, jobs)
        for r in results:
            if r["mcc"] == "5812":
                self.assertEqual(r["decline_reason"], DeclineReason.NOT_QUALIFIED_MEDICAL_EXPENSE)
        approved = [r for r in results if r["status"] == "approved"]
        self.assertEqual(len(approved), 4)
        self.assertTrue(all(r["mcc"] == "5912" for r in approved))
        self.assert_ledger_consistent(acct["id"])

    def test_balance_after_values_form_a_consistent_sequence(self):
        """Each approved purchase must see the balance left by the previous one."""
        acct, card = self.make_account("100")
        results = run_concurrently(self._purchase_on_own_connection, [(card["id"], "7")] * 20)
        approved = sorted((r for r in results if r["status"] == "approved"),
                          key=lambda r: -r["balance_after_cents"])
        expected = [10000 - 700 * (i + 1) for i in range(len(approved))]
        self.assertEqual([r["balance_after_cents"] for r in approved], expected)
        self.assertEqual(len(approved), 14)  # floor(100 / 7)
        # The ledger lists approved purchases newest first, i.e. balance climbing back up.
        listed = [i["balance_after_cents"] for i in services.list_activity(self.conn, acct["id"])
                  if i["kind"] == "purchase" and i["status"] == "approved"]
        self.assertEqual(listed, sorted(listed))

    def test_lock_holding_intervals_never_overlap(self):
        """Direct evidence of serialisation: while one purchase holds the write lock
        (from BEGIN IMMEDIATE returning until its last statement), no other purchase
        holds it. Ordering by lock acquisition must also reproduce the balance sequence."""
        acct, card = self.make_account("100")
        results = run_concurrently(self._purchase_on_own_connection, [(card["id"], "9")] * 25)
        spans = sorted((r["processing"]["lock_acquired_at_ms"], r["processing"]["decided_at_ms"], r)
                       for r in results)
        for (_, end_a, _), (start_b, _, _) in zip(spans, spans[1:]):
            self.assertLessEqual(end_a, start_b, "two purchases held the lock at the same time")
        approved_in_lock_order = [r["balance_after_cents"] for _, _, r in spans
                                  if r["status"] == "approved"]
        self.assertEqual(approved_in_lock_order, [10000 - 900 * (i + 1) for i in range(11)])
        # Some requests really did queue (otherwise the test would not prove much).
        self.assertGreater(max(r["processing"]["lock_wait_ms"] for r in results), 0)

    def test_concurrent_retries_with_same_idempotency_key_debit_once(self):
        acct, card = self.make_account("100")
        results = run_concurrently(self._purchase_on_own_connection,
                                   [(card["id"], "30", "5912", "same-key")] * 15)
        self.assertEqual(len({r["id"] for r in results}), 1)
        self.assertEqual(self.balance_cents(acct["id"]), 7000)

    def test_concurrent_deposits_respect_annual_limit(self):
        acct, _ = self.make_account(with_card=False)   # self-only: $4,400

        def deposit(amount):
            conn = connect(self.db_path)
            try:
                services.deposit(conn, acct["id"], {"amount": amount})
                return "ok"
            except Conflict:
                return "rejected"
            finally:
                conn.close()

        results = run_concurrently(deposit, [("300",)] * 20)
        self.assertEqual(results.count("ok"), 14)      # 14 * 300 = 4200 <= 4400 < 4500
        self.assertEqual(self.balance_cents(acct["id"]), 420000)

    def test_purchases_and_deposits_interleaved(self):
        acct, card = self.make_account("50")

        def job(kind):
            conn = connect(self.db_path)
            try:
                if kind == "deposit":
                    services.deposit(conn, acct["id"], {"amount": "10"})
                else:
                    services.process_transaction(conn, {"card_id": card["id"], "amount": "15",
                                                        "merchant_name": "M", "mcc": "8062"})
            finally:
                conn.close()

        run_concurrently(job, [("deposit",)] * 10 + [("purchase",)] * 10)
        self.assert_ledger_consistent(acct["id"])


class NaiveImplementationControl(DbTestCase):
    """Proves the harness can catch a race: a read-check-write without locking
    approves both the $80 and the $50 purchase against a $100 balance."""

    def naive_purchase(self, account_id, amount_cents):
        conn = connect(self.db_path)
        try:
            balance = conn.execute("SELECT balance_cents FROM accounts WHERE id=?",
                                   (account_id,)).fetchone()[0]
            if balance < amount_cents:
                return "declined"
            time.sleep(0.01)  # any real work between check and write widens the window
            conn.execute("UPDATE accounts SET balance_cents = ? WHERE id = ?",
                         (balance - amount_cents, account_id))
            return "approved"
        finally:
            conn.close()

    def test_naive_read_then_write_loses_money(self):
        acct, _ = self.make_account("100", with_card=False)
        results = run_concurrently(self.naive_purchase, [(acct["id"], 8000), (acct["id"], 5000)])
        # Both approved: $130 spent from a $100 balance. Note the balance is still
        # positive (a lost update), so a CHECK (balance >= 0) alone would NOT catch this.
        self.assertEqual(results, ["approved", "approved"])
        self.assertGreaterEqual(self.balance_cents(acct["id"]), 0)


if __name__ == "__main__":
    unittest.main()

"""Concurrency tests: real OS threads, each with its own SQLite connection,
released together by a barrier, the same shape as simultaneous HTTP requests.

The last class is a control. It runs a deliberately unsafe purchase under the
same harness and checks that it DOES lose money. Without it, passing these
tests would prove nothing: a harness whose threads never overlap passes too.
"""
import random
import time
import unittest

from app import accounts, cards
from app.db import connect
from app.errors import ServiceError
from tests.helpers import DbTestCase, run_concurrently


class ConcurrentPurchaseTests(DbTestCase):
    def test_spec_example_100_balance_80_and_50_at_once(self):
        """The brief's exact scenario, 50 times over to shake out different interleavings."""
        for round_no in range(50):
            with self.subTest(round=round_no):
                acct, card = self.make_account("100")
                results = run_concurrently(self.buy_on_own_connection,
                                           [(card["id"], "80"), (card["id"], "50")])
                approved = [r for r in results if r["status"] == "approved"]
                self.assertEqual(len(approved), 1, results)
                declined = [r for r in results if r["status"] == "declined"]
                self.assertEqual(declined[0]["decline_reason"], "INSUFFICIENT_FUNDS")
                # Whichever got the lock first wins; the balance must match the winner.
                self.assertEqual(self.balance(acct["id"]), 10000 - approved[0]["amount_cents"])
                self.assert_ledger_consistent(acct["id"])

    def test_forty_purchases_at_once_spend_exactly_the_balance(self):
        acct, card = self.make_account("100")
        results = run_concurrently(self.buy_on_own_connection, [(card["id"], "10")] * 40)
        self.assertEqual(sum(r["status"] == "approved" for r in results), 10)
        self.assertEqual(self.balance(acct["id"]), 0)
        self.assert_ledger_consistent(acct["id"])

    def test_one_purchase_holds_the_lock_at_a_time(self):
        """Direct evidence from the server's own timings, in three parts."""
        acct, card = self.make_account("100")
        results = run_concurrently(self.buy_on_own_connection, [(card["id"], "9")] * 25)
        by_lock_order = sorted(results, key=lambda r: r["lock_timing"]["acquired_ms"])
        timing = [r["lock_timing"] for r in by_lock_order]

        # 1. The requests really overlapped: most asked for the lock while
        #    another purchase was holding it. Otherwise this test proves nothing.
        queued = sum(t["requested_ms"] < prev["released_ms"] for prev, t in zip(timing, timing[1:]))
        self.assertGreaterEqual(queued, len(results) // 2, "threads barely overlapped")

        # 2. No two purchases held the lock at once. (decided_ms is the last
        #    moment we certainly hold it; COMMIT releases it after that.)
        for prev, t in zip(timing, timing[1:]):
            self.assertLessEqual(prev["decided_ms"], t["acquired_ms"])

        # 3. Each purchase saw the balance the previous one left.
        approved = [r["balance_after_cents"] for r in by_lock_order if r["status"] == "approved"]
        self.assertEqual(approved, [10000 - 900 * (i + 1) for i in range(11)])
        self.assert_ledger_consistent(acct["id"])

    def test_qualified_and_non_qualified_purchases_at_once(self):
        acct, card = self.make_account("100")
        jobs = [(card["id"], "25", "5912")] * 8 + [(card["id"], "25", "5812")] * 8
        results = run_concurrently(self.buy_on_own_connection, jobs)
        approved = [r for r in results if r["status"] == "approved"]
        self.assertEqual(len(approved), 4)
        self.assertTrue(all(r["mcc"] == "5912" for r in approved))
        self.assertTrue(all(r["decline_reason"] == "NOT_QUALIFIED_MEDICAL_EXPENSE"
                            for r in results if r["mcc"] == "5812"))
        self.assert_ledger_consistent(acct["id"])

    def test_simultaneous_retries_charge_once(self):
        acct, card = self.make_account("100")
        results = run_concurrently(self.buy_on_own_connection, [(card["id"], "30", "5912", "same-key")] * 15)
        self.assertEqual(len({r["id"] for r in results}), 1)
        self.assertEqual(self.balance(acct["id"]), 7000)

    def test_simultaneous_deposits_respect_the_annual_limit(self):
        acct, _ = self.make_account(with_card=False)        # self-only: $4,400

        def deposit(amount):
            conn = connect(self.db_path)
            try:
                accounts.deposit(conn, acct["id"], {"amount": amount})
                return "ok"
            except ServiceError:
                return "refused"
            finally:
                conn.close()

        results = run_concurrently(deposit, [("300",)] * 20)
        self.assertEqual(results.count("ok"), 14)              # 14 x 300 = 4,200 <= 4,400 < 4,500
        self.assertEqual(self.balance(acct["id"]), 420000)

    def test_random_mix_of_everything_keeps_every_account_consistent(self):
        """Deposits, purchases at good and bad merchants, and card freezes, all
        racing across three accounts. Seeded, so a failure can be replayed."""
        rng = random.Random(20261016)
        pairs = [self.make_account("50") for _ in range(3)]
        jobs = []
        for _ in range(90):
            acct, card = rng.choice(pairs)
            kind = rng.choice(["deposit", "buy", "buy", "buy", "freeze", "unfreeze"])
            amount = f"{rng.randint(1, 4000) / 100:.2f}"
            mcc = rng.choice(["5912", "8062", "8021", "5812", "5411"])
            jobs.append((kind, acct["id"], card["id"], amount, mcc))

        def run(kind, account_id, card_id, amount, mcc):
            conn = connect(self.db_path)
            try:
                if kind == "deposit":
                    accounts.deposit(conn, account_id, {"amount": amount})
                elif kind in ("freeze", "unfreeze"):
                    cards.set_frozen(conn, card_id, frozen=(kind == "freeze"))
                else:
                    return self.buy({"id": card_id}, amount, mcc, conn=conn)
            except ServiceError:
                pass          # freezing a frozen card, etc. Refused, nothing changed.
            finally:
                conn.close()

        results = [r for r in run_concurrently(run, jobs) if r]
        self.assertGreater(len(results), 20)
        for acct, _ in pairs:
            self.assert_ledger_consistent(acct["id"])
        for txn in results:
            if txn["status"] == "approved":
                self.assertIn(txn["mcc"], {"5912", "8062", "8021"})
            elif txn["decline_reason"] == "INSUFFICIENT_FUNDS":
                self.assertLess(txn["balance_after_cents"], txn["amount_cents"])

    def test_purchases_and_deposits_interleaved(self):
        acct, card = self.make_account("50")

        def job(kind):
            conn = connect(self.db_path)
            try:
                if kind == "deposit":
                    accounts.deposit(conn, acct["id"], {"amount": "10"})
                else:
                    self.buy({"id": card["id"]}, "15", "8062", conn=conn)
            finally:
                conn.close()

        run_concurrently(job, [("deposit",)] * 10 + [("purchase",)] * 10)
        self.assert_ledger_consistent(acct["id"])


class NaiveImplementationControl(DbTestCase):
    """Shows the harness can catch a race: read, check, then write, with no lock,
    approves both the $80 and the $50 purchase against $100."""

    def naive_purchase(self, account_id, amount_cents):
        conn = connect(self.db_path)
        try:
            balance = conn.execute("SELECT balance_cents FROM accounts WHERE id = ?",
                                   (account_id,)).fetchone()[0]
            if balance < amount_cents:
                return "declined"
            time.sleep(0.01)    # any real work between the check and the write widens the gap
            conn.execute("UPDATE accounts SET balance_cents = ? WHERE id = ?",
                         (balance - amount_cents, account_id))
            return "approved"
        finally:
            conn.close()

    def test_naive_read_then_write_loses_money(self):
        acct, _ = self.make_account("100", with_card=False)
        results = run_concurrently(self.naive_purchase, [(acct["id"], 8000), (acct["id"], 5000)])
        # $130 approved against $100. The balance is still positive (a lost update),
        # so CHECK (balance_cents >= 0) alone would NOT have caught this.
        self.assertEqual(results, ["approved", "approved"])
        self.assertGreaterEqual(self.balance(acct["id"]), 0)


if __name__ == "__main__":
    unittest.main()

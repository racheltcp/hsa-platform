"""Single-threaded business rules: accounts, deposits, cards, purchase decisions."""
import unittest
from datetime import datetime, timezone

from app import services
from app.errors import Conflict, NotFound, ValidationError
from app.money import parse_amount
from app.services import DeclineReason
from tests.helpers import DbTestCase


class MoneyTests(unittest.TestCase):
    def test_parses_dollars_to_cents(self):
        self.assertEqual(parse_amount("12.34"), 1234)
        self.assertEqual(parse_amount(12.34), 1234)   # float noise does not leak in
        self.assertEqual(parse_amount("0.1"), 10)
        self.assertEqual(parse_amount(5), 500)

    def test_formatting_is_integer_exact(self):
        from app.money import fmt, fmt_usd
        self.assertEqual(fmt(5), "0.05")
        self.assertEqual(fmt(10_000_000_000_001), "100000000000.01")
        self.assertEqual(fmt_usd(440000), "$4,400.00")

    def test_rejects_bad_amounts(self):
        for bad in ["0", "-5", "1.234", "abc", "", None, "NaN", "Infinity", True, "1000000.01"]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parse_amount(bad)


class AccountTests(DbTestCase):
    def test_create_account(self):
        acct, _ = self.make_account(with_card=False)
        self.assertTrue(acct["id"].startswith("acct_"))
        self.assertEqual(acct["balance_cents"], 0)
        self.assertIsNone(acct["card"])

    def test_email_is_unique_case_insensitively(self):
        data = {"holder_name": "A", "email": "Dup@Example.com",
                "date_of_birth": "1990-01-01", "coverage_type": "family"}
        services.create_account(self.conn, data)
        with self.assertRaises(Conflict):
            services.create_account(self.conn, {**data, "email": "dup@example.com"})

    def test_validation(self):
        base = {"holder_name": "A", "email": "a@b.co", "date_of_birth": "1990-01-01",
                "coverage_type": "self_only"}
        for field, value in [("holder_name", ""), ("email", "nope"),
                             ("date_of_birth", "01/02/1990"), ("date_of_birth", "2999-01-01"),
                             ("coverage_type", "gold")]:
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                services.create_account(self.conn, {**base, field: value})


class DepositTests(DbTestCase):
    def test_deposit_increases_balance(self):
        acct, _ = self.make_account(with_card=False)
        dep, replayed = services.deposit(self.conn, acct["id"], {"amount": "250.50"})
        self.assertFalse(replayed)
        self.assertEqual(dep["balance_after_cents"], 25050)
        self.assertEqual(self.balance_cents(acct["id"]), 25050)

    def test_deposit_idempotency(self):
        acct, _ = self.make_account(with_card=False)
        services.deposit(self.conn, acct["id"], {"amount": "10", "idempotency_key": "k1"})
        _, replayed = services.deposit(self.conn, acct["id"], {"amount": "10", "idempotency_key": "k1"})
        self.assertTrue(replayed)
        self.assertEqual(self.balance_cents(acct["id"]), 1000)
        with self.assertRaises(Conflict):
            services.deposit(self.conn, acct["id"], {"amount": "99", "idempotency_key": "k1"})

    def test_annual_contribution_limit_self_only(self):
        services.clock = lambda: datetime(2026, 6, 1, tzinfo=timezone.utc)
        acct, _ = self.make_account(with_card=False)
        services.deposit(self.conn, acct["id"], {"amount": "4400"})
        with self.assertRaises(Conflict) as ctx:
            services.deposit(self.conn, acct["id"], {"amount": "0.01"})
        self.assertEqual(ctx.exception.code, "contribution_limit_exceeded")

    def test_family_limit_and_catch_up(self):
        services.clock = lambda: datetime(2026, 6, 1, tzinfo=timezone.utc)
        family, _ = self.make_account(coverage="family", with_card=False)
        self.assertEqual(family["contributions"]["annual_limit_cents"], 875000)
        senior, _ = self.make_account(dob="1971-12-31", with_card=False)  # turns 55 in 2026
        self.assertEqual(senior["contributions"]["annual_limit_cents"], 540000)

    def test_limit_resets_each_tax_year(self):
        services.clock = lambda: datetime(2025, 12, 31, tzinfo=timezone.utc)
        acct, _ = self.make_account(with_card=False)
        services.deposit(self.conn, acct["id"], {"amount": "4300"})
        services.clock = lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
        services.deposit(self.conn, acct["id"], {"amount": "4400"})
        self.assertEqual(self.balance_cents(acct["id"]), 870000)

    def test_unknown_account(self):
        with self.assertRaises(NotFound):
            services.deposit(self.conn, "acct_missing", {"amount": "1"})


class CardTests(DbTestCase):
    def test_issue_card_returns_full_number_once_and_stores_last4(self):
        _, card = self.make_account()
        digits = card["number"].replace(" ", "")
        self.assertEqual(len(digits), 16)
        self.assertEqual(card["last4"], digits[-4:])
        # Luhn-valid
        total = sum(int(d) if i % 2 else (int(d) * 2 - 9 if int(d) * 2 > 9 else int(d) * 2)
                    for i, d in enumerate(reversed(digits[:-1])))
        self.assertEqual((10 - total % 10) % 10, int(digits[-1]))
        # Full number is never persisted
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(cards)")]
        self.assertNotIn("number", cols)
        self.assertNotIn("pan", cols)

    def test_one_live_card_per_account(self):
        acct, first = self.make_account()
        with self.assertRaises(Conflict):
            services.issue_card(self.conn, acct["id"])
        second = services.issue_card(self.conn, acct["id"], {"replace": True})
        old = self.conn.execute("SELECT status FROM cards WHERE id=?", (first["id"],)).fetchone()[0]
        self.assertEqual(old, "cancelled")
        self.assertEqual(services.get_account(self.conn, acct["id"])["card"]["id"], second["id"])

    def test_freeze_and_unfreeze(self):
        _, card = self.make_account("100")
        services.set_card_status(self.conn, card["id"], "frozen")
        self.assertEqual(self.purchase(card, "10")["decline_reason"], DeclineReason.CARD_FROZEN)
        services.set_card_status(self.conn, card["id"], "active")
        self.assertEqual(self.purchase(card, "10")["status"], "approved")


class TransactionTests(DbTestCase):
    def test_qualified_vs_non_qualified_merchants(self):
        acct, card = self.make_account("1000")
        expected = {
            "5912": "approved",  # Pharmacy
            "8062": "approved",  # Hospital
            "8021": "approved",  # Dentist
            "5812": "declined",  # Restaurant
            "5732": "declined",  # Electronics
            "5411": "declined",  # Grocery
            "1234": "declined",  # Unknown MCC: fail closed
        }
        for mcc, status in expected.items():
            with self.subTest(mcc=mcc):
                txn = self.purchase(card, "10", mcc=mcc)
                self.assertEqual(txn["status"], status)
                if status == "declined":
                    self.assertEqual(txn["decline_reason"],
                                     DeclineReason.NOT_QUALIFIED_MEDICAL_EXPENSE)
        self.assertEqual(self.balance_cents(acct["id"]), 100000 - 3 * 1000)
        self.assert_ledger_consistent(acct["id"])

    def test_spec_example_sequential(self):
        acct, card = self.make_account("100")
        self.assertEqual(self.purchase(card, "80")["status"], "approved")
        b = self.purchase(card, "50")
        self.assertEqual((b["status"], b["decline_reason"]),
                         ("declined", DeclineReason.INSUFFICIENT_FUNDS))
        self.assertEqual(self.balance_cents(acct["id"]), 2000)

    def test_exact_balance_is_approved(self):
        acct, card = self.make_account("42.17")
        self.assertEqual(self.purchase(card, "42.17")["status"], "approved")
        self.assertEqual(self.balance_cents(acct["id"]), 0)

    def test_non_qualified_check_happens_before_funds_check(self):
        _, card = self.make_account("5")
        txn = self.purchase(card, "500", mcc="5812")
        self.assertEqual(txn["decline_reason"], DeclineReason.NOT_QUALIFIED_MEDICAL_EXPENSE)

    def test_declines_are_recorded(self):
        acct, card = self.make_account()
        self.purchase(card, "1")
        n = self.conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id=?",
                              (acct["id"],)).fetchone()[0]
        self.assertEqual(n, 1)

    def test_expired_card(self):
        services.clock = lambda: datetime(2026, 1, 15, tzinfo=timezone.utc)
        _, card = self.make_account("100")       # expires 01/2029
        services.clock = lambda: datetime(2029, 1, 31, tzinfo=timezone.utc)
        self.assertEqual(self.purchase(card, "1")["status"], "approved")  # valid through end of month
        services.clock = lambda: datetime(2029, 2, 1, tzinfo=timezone.utc)
        self.assertEqual(self.purchase(card, "1")["decline_reason"], DeclineReason.CARD_EXPIRED)

    def test_cancelled_card(self):
        acct, card = self.make_account("100")
        services.issue_card(self.conn, acct["id"], {"replace": True})
        self.assertEqual(self.purchase(card, "1")["decline_reason"], DeclineReason.CARD_CANCELLED)

    def test_transaction_idempotency(self):
        acct, card = self.make_account("100")
        first = self.purchase(card, "30", key="net-retry-1")
        txn, replayed = services.process_transaction(self.conn, {
            "card_id": card["id"], "amount": "30", "merchant_name": "Test Merchant",
            "mcc": "5912", "idempotency_key": "net-retry-1"})
        self.assertTrue(replayed)
        self.assertEqual(txn["id"], first["id"])
        self.assertEqual(self.balance_cents(acct["id"]), 7000)  # debited once
        with self.assertRaises(Conflict):
            self.purchase(card, "31", key="net-retry-1")

    def test_unknown_card_and_bad_input(self):
        with self.assertRaises(NotFound):
            services.process_transaction(self.conn, {"card_id": "card_x", "amount": "1",
                                                     "merchant_name": "M", "mcc": "5912"})
        _, card = self.make_account("10")
        for bad in [{"mcc": "59"}, {"mcc": "abcd"}, {"amount": "-1"}, {"merchant_name": ""}]:
            data = {"card_id": card["id"], "amount": "1", "merchant_name": "M", "mcc": "5912", **bad}
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                services.process_transaction(self.conn, data)

    def test_database_rejects_negative_balance_even_if_app_logic_is_bypassed(self):
        acct, _ = self.make_account("10")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE accounts SET balance_cents = -1 WHERE id = ?", (acct["id"],))


if __name__ == "__main__":
    unittest.main()

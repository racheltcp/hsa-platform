"""Business rules, one request at a time. Concurrency lives in test_concurrency.py."""
import sqlite3
import unittest

from app import accounts, cards, purchases
from app.errors import Conflict, NotFound, ValidationError
from app.money import fmt_usd, parse_amount
from tests.helpers import DbTestCase


class MoneyTests(unittest.TestCase):
    def test_parses_dollars_to_cents(self):
        cases = {"12.34": 1234, "0.1": 10, "12.340": 1234, "100": 10000, "1E2": 10000, 5: 500}
        for given, cents in cases.items():
            with self.subTest(given=given):
                self.assertEqual(parse_amount(given), cents)
        self.assertEqual(parse_amount(12.34), 1234)          # float noise does not leak in

    def test_rejects_bad_amounts(self):
        bad = ["0", "-5", "-0", "1.234", "abc", "", None, True, "NaN", "Infinity", "100000.01",
               "1e999999999",    # once crashed the server: rounding overflowed
               "1e-999999999"]   # once parsed as 0 cents: normalize() underflowed
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValidationError) as ctx:
                parse_amount(value)
            self.assertEqual(ctx.exception.field, "amount")

    def test_formats_without_floats(self):
        self.assertEqual(fmt_usd(5), "$0.05")
        self.assertEqual(fmt_usd(440000), "$4,400.00")


class AccountTests(DbTestCase):
    def test_create_account(self):
        acct, _ = self.make_account(with_card=False)
        self.assertTrue(acct["id"].startswith("acct_"))
        self.assertEqual(acct["balance_cents"], 0)
        self.assertIsNone(acct["card"])

    def test_email_is_unique_ignoring_case(self):
        data = {"holder_name": "A", "email": "Dup@Example.com", "date_of_birth": "1990-01-01",
                "coverage_type": "family"}
        accounts.create_account(self.conn, data)
        with self.assertRaises(Conflict) as ctx:
            accounts.create_account(self.conn, {**data, "email": "dup@example.com"})
        self.assertEqual(ctx.exception.field, "email")

    def test_validation_names_the_field(self):
        base = {"holder_name": "A", "email": "a@b.co", "date_of_birth": "1990-01-01",
                "coverage_type": "self_only"}
        for field, value in [("holder_name", ""), ("email", "nope"), ("date_of_birth", "01/02/1990"),
                             ("date_of_birth", "2999-01-01"), ("coverage_type", "gold")]:
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError) as ctx:
                accounts.create_account(self.conn, {**base, field: value})
            self.assertEqual(ctx.exception.field, field)


class DepositTests(DbTestCase):
    def test_deposit_increases_balance(self):
        acct, _ = self.make_account(with_card=False)
        dep, replayed = accounts.deposit(self.conn, acct["id"], {"amount": "250.50"})
        self.assertFalse(replayed)
        self.assertEqual(dep["balance_after_cents"], 25050)
        self.assertEqual(self.balance(acct["id"]), 25050)

    def test_retry_with_same_key_credits_once(self):
        acct, _ = self.make_account(with_card=False)
        accounts.deposit(self.conn, acct["id"], {"amount": "10", "idempotency_key": "k1"})
        _, replayed = accounts.deposit(self.conn, acct["id"], {"amount": "10", "idempotency_key": "k1"})
        self.assertTrue(replayed)
        self.assertEqual(self.balance(acct["id"]), 1000)
        with self.assertRaises(Conflict):
            accounts.deposit(self.conn, acct["id"], {"amount": "99", "idempotency_key": "k1"})

    def test_keys_are_scoped_to_the_account(self):
        # Regression: keys used to be global, so B got a 409 for a key A had used.
        a, _ = self.make_account(with_card=False)
        b, _ = self.make_account(with_card=False)
        accounts.deposit(self.conn, a["id"], {"amount": "1", "idempotency_key": "shared"})
        _, replayed = accounts.deposit(self.conn, b["id"], {"amount": "5", "idempotency_key": "shared"})
        self.assertFalse(replayed)
        self.assertEqual(self.balance(b["id"]), 500)

    def test_annual_limit_self_only(self):
        self.set_today(2026, 6, 1)
        acct, _ = self.make_account(with_card=False)
        accounts.deposit(self.conn, acct["id"], {"amount": "4400"})
        with self.assertRaises(Conflict) as ctx:
            accounts.deposit(self.conn, acct["id"], {"amount": "0.01"})
        self.assertEqual((ctx.exception.code, ctx.exception.field), ("contribution_limit_exceeded", "amount"))

    def test_family_limit_and_catch_up(self):
        self.set_today(2026, 6, 1)
        family, _ = self.make_account(coverage="family", with_card=False)
        self.assertEqual(family["contributions"]["limit_cents"], 875000)
        turning_55, _ = self.make_account(dob="1971-12-31", with_card=False)   # 55 by Dec 31, 2026
        self.assertEqual(turning_55["contributions"]["limit_cents"], 540000)

    def test_limit_resets_each_tax_year(self):
        self.set_today(2025, 12, 31)
        acct, _ = self.make_account(with_card=False)
        accounts.deposit(self.conn, acct["id"], {"amount": "4300"})
        self.set_today(2026, 1, 1)
        accounts.deposit(self.conn, acct["id"], {"amount": "4400"})
        self.assertEqual(self.balance(acct["id"]), 870000)

    def test_unknown_account(self):
        with self.assertRaises(NotFound):
            accounts.deposit(self.conn, "acct_missing", {"amount": "1"})


class CardTests(DbTestCase):
    def test_number_is_luhn_valid_returned_once_and_never_stored(self):
        acct, card = self.make_account()
        digits = card["number"].replace(" ", "")
        self.assertEqual((len(digits), card["last4"]), (16, digits[-4:]))
        total = sum(int(d) if i % 2 == 0 else (int(d) * 2 - 9 if int(d) * 2 > 9 else int(d) * 2)
                    for i, d in enumerate(reversed(digits)))
        self.assertEqual(total % 10, 0)
        self.assertNotIn("number", accounts.get_account(self.conn, acct["id"])["card"])
        stored = self.conn.execute("SELECT * FROM cards WHERE id = ?", (card["id"],)).fetchone()
        self.assertNotIn(digits, [str(v) for v in tuple(stored)])

    def test_one_live_card_and_replacing_cancels_the_old_one(self):
        acct, first = self.make_account("100")
        with self.assertRaises(Conflict):
            cards.issue_card(self.conn, acct["id"])
        second = cards.issue_card(self.conn, acct["id"], replace=True)
        self.assertEqual(accounts.get_account(self.conn, acct["id"])["card"]["id"], second["id"])
        self.assertEqual(self.buy(first, "1")["decline_reason"], "CARD_CANCELLED")

    def test_freeze_and_unfreeze(self):
        _, card = self.make_account("100")
        cards.set_frozen(self.conn, card["id"], frozen=True)
        self.assertEqual(self.buy(card, "10")["decline_reason"], "CARD_FROZEN")
        with self.assertRaises(Conflict):
            cards.set_frozen(self.conn, card["id"], frozen=True)
        cards.set_frozen(self.conn, card["id"], frozen=False)
        self.assertEqual(self.buy(card, "10")["status"], "approved")


class PurchaseTests(DbTestCase):
    def test_qualified_and_non_qualified_merchants(self):
        acct, card = self.make_account("1000")
        expected = {"5912": "approved", "8062": "approved", "8021": "approved",   # pharmacy, hospital, dentist
                    "5812": "declined", "5732": "declined", "5411": "declined",   # restaurant, electronics, grocery
                    "1234": "declined"}                                           # unknown: fails closed
        for mcc, status in expected.items():
            with self.subTest(mcc=mcc):
                self.assertEqual(self.buy(card, "10", mcc=mcc)["status"], status)
        self.assertEqual(self.balance(acct["id"]), 100000 - 3 * 1000)
        self.assert_ledger_consistent(acct["id"])

    def test_spec_example_one_after_the_other(self):
        acct, card = self.make_account("100")
        self.assertEqual(self.buy(card, "80")["status"], "approved")
        self.assertEqual(self.buy(card, "50")["decline_reason"], "INSUFFICIENT_FUNDS")
        self.assertEqual(self.balance(acct["id"]), 2000)

    def test_exact_balance_is_approved(self):
        acct, card = self.make_account("42.17")
        self.assertEqual(self.buy(card, "42.17")["status"], "approved")
        self.assertEqual(self.balance(acct["id"]), 0)

    def test_each_check_fails_alone_and_the_trace_points_at_it(self):
        """Builds one purchase that fails each check and nothing before it, and
        confirms the stored reason and the rebuilt trace agree on where it stopped."""
        names = [c.name for c in purchases.CHECKS]
        scenarios = {}

        acct, card = self.make_account("100")
        cards.issue_card(self.conn, acct["id"], replace=True)
        scenarios["card_not_cancelled"] = self.buy(card, "1")

        _, card = self.make_account("100")
        cards.set_frozen(self.conn, card["id"], frozen=True)
        scenarios["card_not_frozen"] = self.buy(card, "1")

        self.set_today(2026, 1, 15)
        _, card = self.make_account("100")            # expires 01/2029
        self.set_today(2029, 2, 1)
        scenarios["card_not_expired"] = self.buy(card, "1")
        self.set_today(2026, 6, 1)

        _, card = self.make_account("100")
        scenarios["merchant_qualified"] = self.buy(card, "1", mcc="5812")
        scenarios["sufficient_funds"] = self.buy(card, "500")

        self.assertEqual(set(scenarios), set(names), "every check needs a scenario")
        for name, txn in scenarios.items():
            with self.subTest(check=name):
                i = names.index(name)
                self.assertEqual(txn["decline_reason"], purchases.CHECKS[i].decline_reason)
                self.assertEqual([c["status"] for c in txn["decision"]],
                                 ["passed"] * i + ["failed"] + ["skipped"] * (len(names) - i - 1))

        approved = self.buy(card, "5")
        self.assertEqual([c["status"] for c in approved["decision"]], ["passed"] * len(names))
        self.assertIn("leaving $95.00", approved["decision"][-1]["detail"])

    def test_check_order_decides_which_reason_wins(self):
        _, card = self.make_account("5")
        self.assertEqual(self.buy(card, "500", mcc="5812")["decline_reason"],
                         "NOT_QUALIFIED_MEDICAL_EXPENSE")          # merchant before funds
        cards.set_frozen(self.conn, card["id"], frozen=True)
        self.assertEqual(self.buy(card, "500", mcc="5812")["decline_reason"], "CARD_FROZEN")

    def test_card_works_through_the_end_of_its_expiry_month(self):
        self.set_today(2026, 1, 15)
        _, card = self.make_account("100")
        self.set_today(2029, 1, 31)
        self.assertEqual(self.buy(card, "1")["status"], "approved")

    def test_every_attempt_is_recorded(self):
        acct, card = self.make_account()
        self.buy(card, "1")
        self.buy(card, "1", mcc="5812")
        n = self.conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id = ?",
                              (acct["id"],)).fetchone()[0]
        self.assertEqual(n, 2)

    def test_retry_returns_the_original_result_and_charges_once(self):
        acct, card = self.make_account("100")
        first = self.buy(card, "30", key="net-retry-1")
        again, replayed = purchases.authorize_purchase(self.conn, {
            "card_id": card["id"], "amount": "30", "merchant_name": "Test Merchant", "mcc": "5912",
            "idempotency_key": "net-retry-1"})
        self.assertTrue(replayed)
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(self.balance(acct["id"]), 7000)

    def test_retry_key_reused_for_a_different_purchase_is_refused(self):
        _, card = self.make_account("100")
        self.buy(card, "30", key="k")
        for changed in [{"amount": "31"}, {"mcc": "8062"},
                        {"merchant": "Somewhere Else"}]:   # regression: merchant used to be ignored
            with self.subTest(changed=changed), self.assertRaises(Conflict):
                self.buy(card, **{"amount": "30", "key": "k", **changed})

    def test_retry_keys_are_scoped_to_the_card(self):
        _, card_a = self.make_account("100")
        _, card_b = self.make_account("100")
        self.buy(card_a, "10", key="same")
        self.assertEqual(self.buy(card_b, "20", key="same")["amount_cents"], 2000)

    def test_unknown_card_and_bad_input(self):
        with self.assertRaises(NotFound):
            self.buy({"id": "card_missing"}, "1")
        _, card = self.make_account("10")
        for bad, field in [({"mcc": "59"}, "mcc"), ({"mcc": "abcd"}, "mcc"), ({"amount": "-1"}, "amount"),
                           ({"merchant_name": ""}, "merchant_name")]:
            data = {"card_id": card["id"], "amount": "1", "merchant_name": "M", "mcc": "5912", **bad}
            with self.subTest(bad=bad), self.assertRaises(ValidationError) as ctx:
                purchases.authorize_purchase(self.conn, data)
            self.assertEqual(ctx.exception.field, field)

    def test_database_itself_refuses_a_negative_balance(self):
        acct, _ = self.make_account("10")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE accounts SET balance_cents = -1 WHERE id = ?", (acct["id"],))


if __name__ == "__main__":
    unittest.main()

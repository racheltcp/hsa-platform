"""HTTP API: the request/response contract, error shapes, and a race through a
real multi-threaded HTTP server (the same server mode as `python run.py`)."""
import json
import logging
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from werkzeug.serving import make_server

from app import accounts, create_app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client = create_app(str(Path(self._tmp) / "api.db")).test_client()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def setup_account(self, deposit="100"):
        r = self.client.post("/api/accounts", json={
            "holder_name": "Grace Hopper", "email": "grace@example.com",
            "date_of_birth": "1985-12-09", "coverage_type": "family"})
        self.assertEqual(r.status_code, 201)
        acct = r.get_json()
        self.assertEqual(self.client.post(f"/api/accounts/{acct['id']}/deposits",
                                          json={"amount": deposit}).status_code, 201)
        card = self.client.post(f"/api/accounts/{acct['id']}/cards", json={}).get_json()
        return acct, card

    def test_full_flow(self):
        acct, card = self.setup_account()
        r = self.client.post("/api/transactions", json={
            "card_id": card["id"], "amount": "80", "merchant_name": "Corner Pharmacy", "mcc": "5912"})
        self.assertEqual((r.status_code, r.get_json()["status"]), (201, "approved"))
        r = self.client.post("/api/transactions", json={
            "card_id": card["id"], "amount": "5", "merchant_name": "Bistro", "mcc": "5812"})
        self.assertEqual(r.status_code, 201)            # a decline is a processed request, not an error
        self.assertEqual(r.get_json()["decline_reason"], "NOT_QUALIFIED_MEDICAL_EXPENSE")
        detail = self.client.get(f"/api/accounts/{acct['id']}").get_json()
        self.assertEqual(detail["balance_cents"], 2000)
        self.assertNotIn("number", detail["card"])      # full number only at issuance
        activity = self.client.get(f"/api/accounts/{acct['id']}/activity").get_json()
        self.assertEqual([a["kind"] for a in activity], ["purchase", "purchase", "deposit"])

    def test_errors_are_json_with_a_readable_message_and_field(self):
        r = self.client.post("/api/accounts", json={"holder_name": ""})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["error"]["field"], "holder_name")
        self.assertEqual(r.get_json()["error"]["message"], "Full name is required.")
        self.assertEqual(self.client.get("/api/accounts/acct_nope").status_code, 404)

    def test_malformed_json_is_reported_as_such(self):
        # Regression: used to come back as "holder_name is required".
        for raw in ["{bad", "[1, 2]", "null"]:
            with self.subTest(raw=raw):
                r = self.client.post("/api/accounts", data=raw, content_type="application/json")
                self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, "invalid_json"))

    def test_absurd_amount_is_a_validation_error_not_a_crash(self):
        # Regression: 1e999999999 used to raise inside the decimal module (HTML 500).
        acct, _ = self.setup_account()
        r = self.client.post(f"/api/accounts/{acct['id']}/deposits", json={"amount": "1e999999999"})
        self.assertEqual((r.status_code, r.get_json()["error"]["field"]), (422, "amount"))

    def test_unexpected_failures_still_answer_in_json(self):
        with mock.patch.object(accounts, "list_accounts", side_effect=RuntimeError("boom")):
            r = self.client.get("/api/accounts")
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (500, "internal_error"))
        with mock.patch.object(accounts, "list_accounts",
                               side_effect=sqlite3.OperationalError("database is locked")):
            r = self.client.get("/api/accounts")
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (503, "busy"))
        for r, status in [(self.client.delete("/api/accounts"), 405), (self.client.get("/api/nope"), 404)]:
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (status, "http_error"))

    def test_replay_is_200(self):
        _, card = self.setup_account()
        body = {"card_id": card["id"], "amount": "10", "merchant_name": "M", "mcc": "5912",
                "idempotency_key": "abc"}
        self.assertEqual(self.client.post("/api/transactions", json=body).status_code, 201)
        self.assertEqual(self.client.post("/api/transactions", json=body).status_code, 200)

    def test_card_endpoints(self):
        acct, card = self.setup_account()
        self.assertEqual(self.client.post(f"/api/cards/{card['id']}/freeze").get_json()["status"], "frozen")
        self.assertEqual(self.client.post(f"/api/cards/{card['id']}/freeze").status_code, 409)
        self.assertEqual(self.client.post(f"/api/cards/{card['id']}/unfreeze").get_json()["status"], "active")
        self.assertEqual(self.client.post(f"/api/accounts/{acct['id']}/cards", json={}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/accounts/{acct['id']}/cards",
                                          json={"replace": True}).status_code, 201)

    def test_merchant_categories(self):
        cats = {c["mcc"]: c["qualified"] for c in self.client.get("/api/merchant-categories").get_json()}
        self.assertEqual((cats["5912"], cats["8062"], cats["5812"], cats["5732"]), (True, True, False, False))


class RealServerRaceTest(unittest.TestCase):
    """Thirty simultaneous HTTP requests against a real threaded server."""

    def setUp(self):
        logging.getLogger("werkzeug").setLevel(logging.ERROR)    # no per-request log lines
        self._tmp = tempfile.mkdtemp()
        self.server = make_server("127.0.0.1", 0, create_app(str(Path(self._tmp) / "race.db")), threaded=True)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def call(self, path, body=None):
        req = urllib.request.Request(
            self.base + path, method="POST" if body is not None else "GET",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_thirty_simultaneous_purchases(self):
        _, acct = self.call("/api/accounts", {"holder_name": "R", "email": "r@example.com",
                                              "date_of_birth": "1990-01-01", "coverage_type": "self_only"})
        self.call(f"/api/accounts/{acct['id']}/deposits", {"amount": "100"})
        _, card = self.call(f"/api/accounts/{acct['id']}/cards", {})
        barrier = threading.Barrier(30)

        def buy(_):
            barrier.wait()
            return self.call("/api/transactions", {"card_id": card["id"], "amount": "10",
                                                   "merchant_name": "Pharmacy", "mcc": "5912"})

        with ThreadPoolExecutor(30) as pool:
            results = list(pool.map(buy, range(30)))
        self.assertTrue(all(code == 201 for code, _ in results), results)
        self.assertEqual(sum(t["status"] == "approved" for _, t in results), 10)
        self.assertEqual(self.call(f"/api/accounts/{acct['id']}")[1]["balance_cents"], 0)


if __name__ == "__main__":
    unittest.main()

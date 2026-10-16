"""HTTP API tests: request/response contract, and a race through a real
multi-threaded HTTP server (same server mode as `python run.py`)."""
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from werkzeug.serving import make_server

from app import create_app


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client = create_app(str(Path(self._tmp) / "api.db")).test_client()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _setup_account(self, deposit="100"):
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
        acct, card = self._setup_account()
        r = self.client.post("/api/transactions", json={
            "card_id": card["id"], "amount": "80", "merchant_name": "Corner Pharmacy", "mcc": "5912"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["status"], "approved")
        r = self.client.post("/api/transactions", json={
            "card_id": card["id"], "amount": "5", "merchant_name": "Bistro", "mcc": "5812"})
        self.assertEqual(r.status_code, 201)            # declines are processed requests
        self.assertEqual(r.get_json()["decline_reason"], "NOT_QUALIFIED_MEDICAL_EXPENSE")
        detail = self.client.get(f"/api/accounts/{acct['id']}").get_json()
        self.assertEqual(detail["balance"], "20.00")
        self.assertEqual(detail["card"]["id"], card["id"])
        self.assertNotIn("number", detail["card"])      # full PAN only on issuance
        activity = self.client.get(f"/api/accounts/{acct['id']}/activity").get_json()
        self.assertEqual([a["kind"] for a in activity].count("purchase"), 2)
        self.assertEqual(len(self.client.get("/api/accounts").get_json()), 1)

    def test_error_shapes(self):
        r = self.client.post("/api/accounts", json={"holder_name": ""})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["error"]["code"], "validation_error")
        self.assertEqual(self.client.get("/api/accounts/acct_nope").status_code, 404)
        self.assertEqual(self.client.post("/api/transactions", json={
            "card_id": "card_nope", "amount": "1", "merchant_name": "M", "mcc": "5912"}).status_code, 404)
        r = self.client.post("/api/accounts", data="[1,2]", content_type="application/json")
        self.assertEqual(r.status_code, 422)

    def test_idempotent_replay_returns_200(self):
        _, card = self._setup_account()
        body = {"card_id": card["id"], "amount": "10", "merchant_name": "M", "mcc": "5912",
                "idempotency_key": "abc"}
        self.assertEqual(self.client.post("/api/transactions", json=body).status_code, 201)
        self.assertEqual(self.client.post("/api/transactions", json=body).status_code, 200)

    def test_card_freeze_endpoints(self):
        acct, card = self._setup_account()
        self.assertEqual(self.client.post(f"/api/cards/{card['id']}/freeze").get_json()["status"], "frozen")
        self.assertEqual(self.client.post(f"/api/cards/{card['id']}/freeze").status_code, 409)
        self.assertEqual(self.client.post(f"/api/cards/{card['id']}/unfreeze").get_json()["status"], "active")
        self.assertEqual(self.client.post(f"/api/accounts/{acct['id']}/cards", json={}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/accounts/{acct['id']}/cards",
                                          json={"replace": True}).status_code, 201)

    def test_merchant_categories(self):
        cats = {c["mcc"]: c for c in self.client.get("/api/merchant-categories").get_json()}
        self.assertTrue(cats["5912"]["qualified"])
        self.assertTrue(cats["8062"]["qualified"])
        self.assertFalse(cats["5812"]["qualified"])
        self.assertFalse(cats["5732"]["qualified"])


class RealServerRaceTest(unittest.TestCase):
    """Fire simultaneous HTTP requests at a real threaded server."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        app = create_app(str(Path(self._tmp) / "race.db"))
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=30) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_concurrent_http_purchases(self):
        _, acct = self.post("/api/accounts", {"holder_name": "R", "email": "r@example.com",
                                              "date_of_birth": "1990-01-01", "coverage_type": "self_only"})
        self.post(f"/api/accounts/{acct['id']}/deposits", {"amount": "100"})
        _, card = self.post(f"/api/accounts/{acct['id']}/cards", {})
        barrier = threading.Barrier(30)

        def buy(_):
            barrier.wait()
            return self.post("/api/transactions", {"card_id": card["id"], "amount": "10",
                                                   "merchant_name": "Pharmacy", "mcc": "5912"})

        with ThreadPoolExecutor(30) as pool:
            results = list(pool.map(buy, range(30)))
        self.assertTrue(all(code == 201 for code, _ in results), results)
        self.assertEqual(sum(t["status"] == "approved" for _, t in results), 10)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{self.base}/api/accounts/{acct['id']}") as resp:
            self.assertEqual(json.load(resp)["balance"], "0.00")


if __name__ == "__main__":
    unittest.main()

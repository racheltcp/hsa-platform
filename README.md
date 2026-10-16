# HSA Platform

A small Health Savings Account platform: create accounts, deposit funds (within IRS
contribution limits), issue virtual debit cards, and authorize card purchases, approving
only qualified medical expenses and never letting a balance go negative under concurrent load.

- **Backend:** Python 3.10+, Flask, SQLite (stdlib `sqlite3`, no ORM)
- **Frontend:** a single page of plain HTML/CSS/JS served by Flask (no build step)
- **Tests:** stdlib `unittest`, including multi-threaded race tests and a mutation check

Docs: [architecture.md](architecture.md) · [ai-usage.md](ai-usage.md) · [demo video](#demo-video)

---

## Run it locally

```bash
git clone <this repo> && cd hsa-platform
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Open **http://localhost:5000**. Data is stored in `data/hsa.db` (created on first run).
To start fresh, stop the server and delete the `data/` folder.

Options: `python run.py --port 8000 --db /path/to/other.db`

Requirements: Python 3.10 or newer (its bundled SQLite must be 3.35+, which every
official Python 3.10+ build includes; check with `python -c "import sqlite3; print(sqlite3.sqlite_version)"`).

## Reviewer's tour (about 2 minutes)

1. **Click "Run the $100 / $80 + $50 race"** (top right). This creates a fresh account,
   deposits $100, issues a card, then fires an $80 and a $50 pharmacy purchase *at the same
   moment*. Exactly one is approved, the other is declined for insufficient funds, and the
   ledger check confirms the balance is never negative. Click it a few times: which purchase
   wins depends on arrival order, and both outcomes are correct.
2. **Create an account** in the left panel (name, email, date of birth, HDHP coverage type).
3. **Deposit funds.** Try depositing more than $4,400 in total on a self-only account to see
   the IRS annual limit enforced.
4. **Issue a virtual card.** The full number is shown once; only the last 4 digits are stored.
5. **Simulate purchases** with the merchant presets: Pharmacy, Hospital, Dentist and
   Eyeglasses are approved; Restaurant, Electronics and Grocery are declined as non-qualified.
6. **Freeze the card** and try another purchase (declined: card frozen). Unfreeze to resume.
7. **Concurrency lab:** e.g. amounts `10`, repeat `20` against a $100 balance. All 20 fire
   simultaneously; exactly 10 are approved.
8. The **Activity** table shows every deposit and every purchase attempt, approved or declined.

## Run the tests

```bash
python -m unittest discover -s tests -t . -v      # 39 tests, ~5 seconds
python scripts/mutation_check.py                  # proves the race tests can catch races
```

`mutation_check.py` copies the project to a temp folder, deliberately breaks the concurrency
controls one at a time, and confirms the concurrency tests fail for every unsafe variant:

```
[ok] baseline (no mutation)                                     -> tests pass
[ok] lock only: BEGIN IMMEDIATE + read-check-write in Python    -> tests pass
[ok] BEGIN (deferred) instead of BEGIN IMMEDIATE                -> tests FAIL
[ok] atomic UPDATE only: no transaction lock                    -> tests FAIL
[ok] naive: no lock + read-check-write                          -> tests FAIL
```

## API

All endpoints take and return JSON. Amounts are sent as decimal strings (`"12.34"`) and
returned both as strings and integer cents.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/accounts` | Create account: `holder_name`, `email`, `date_of_birth` (YYYY-MM-DD), `coverage_type` (`self_only` / `family`) |
| `GET` | `/api/accounts` | List accounts with balance, contribution status and live card |
| `GET` | `/api/accounts/{id}` | One account |
| `GET` | `/api/accounts/{id}/activity` | Deposits and purchase attempts, newest first |
| `POST` | `/api/accounts/{id}/deposits` | `amount`, optional `idempotency_key` |
| `POST` | `/api/accounts/{id}/cards` | Issue a virtual card; `{"replace": true}` cancels and replaces the current one |
| `POST` | `/api/cards/{id}/freeze` · `/unfreeze` | Toggle card |
| `POST` | `/api/transactions` | Authorize a purchase: `card_id`, `amount`, `merchant_name`, `mcc`, optional `idempotency_key` |
| `GET` | `/api/merchant-categories` | Known MCCs and whether each is qualified |

Status codes: `201` created (including **declined** purchases, which are a valid business
outcome, not an error), `200` idempotent replay, `404` unknown account/card, `409` conflict
(duplicate email, contribution limit, card already issued, reused idempotency key),
`422` invalid input. Errors look like `{"error": {"code": "...", "message": "..."}}`.

Example with curl:

```bash
ACCT=$(curl -s localhost:5000/api/accounts -H 'Content-Type: application/json' \
  -d '{"holder_name":"Ada Lovelace","email":"ada@example.com","date_of_birth":"1985-12-10","coverage_type":"self_only"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -s localhost:5000/api/accounts/$ACCT/deposits -H 'Content-Type: application/json' -d '{"amount":"100"}'
CARD=$(curl -s -X POST localhost:5000/api/accounts/$ACCT/cards -H 'Content-Type: application/json' -d '{}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -s localhost:5000/api/transactions -H 'Content-Type: application/json' \
  -d "{\"card_id\":\"$CARD\",\"amount\":\"80\",\"merchant_name\":\"Corner Pharmacy\",\"mcc\":\"5912\"}"
```

## Project layout

```
app/
  __init__.py      Flask app factory
  api.py           HTTP routes (thin: parse JSON, call services, shape responses)
  services.py      Business logic: accounts, deposits, cards, purchase authorization
  db.py            Connections + BEGIN IMMEDIATE write transactions
  schema.sql       Tables, CHECK constraints, partial unique index
  merchants.py     MCC allow-list for qualified medical expenses
  limits.py        IRS annual contribution limits
  money.py         Dollar string <-> integer cents
  static/          index.html, app.js, styles.css
tests/
  test_business_rules.py   Single-threaded rules
  test_concurrency.py      Thread races + a naive-implementation control
  test_http.py             API contract + races through a real threaded HTTP server
scripts/mutation_check.py  Verifies the race tests detect broken concurrency control
```

## Demo video

<!-- TODO: paste your video link here -->
Video: _link_

An outline for recording it is in [docs/demo-video-outline.md](docs/demo-video-outline.md).

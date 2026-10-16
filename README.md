# HSA Platform

A small Health Savings Account platform: create accounts, deposit funds (within IRS
contribution limits), issue virtual debit cards, and authorize card purchases, approving
only qualified medical expenses and never letting a balance go negative under concurrent load.

- **Backend:** Python 3.9+, Flask, SQLite (stdlib `sqlite3`, no ORM)
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

Open **http://localhost:8000**. (Not 5000: macOS's AirPlay Receiver already uses that port.)
Data is stored in `data/hsa.db`, created on first run. To start fresh, stop the server and
delete the `data/` folder. If you ran an earlier version of this project, the app will say
its database is out of date: delete `data/` and start again.

Options: `python run.py --port 9000 --db /path/to/other.db`

Requirements: Python 3.9 or newer (including the `python3` that ships with macOS) whose
SQLite is 3.35+. `run.py` checks both at startup and tells you if either is too old.
On macOS use `python3`/`pip3` outside the virtual environment; inside it, `python`/`pip` work.

## Reviewer's tour (about 2 minutes)

1. **Click "Run the $100 race"** (top right). This creates a fresh account, deposits $100,
   issues a card, then fires an $80 and a $50 pharmacy purchase *at the same moment*. Exactly
   one is approved. The timeline shows, with timings measured on the server, the second
   purchase waiting for the account's lock while the first holds it, then seeing only $20
   left. Click it a few times: which purchase wins depends on arrival order, and both
   outcomes are correct.
2. **Create an account** with "New account" (name, email, date of birth, health plan coverage).
   Try a future date of birth or a duplicate email: the error appears next to the field.
3. **Deposit funds.** Try depositing more than $4,400 in total on a self-only account to see
   the IRS annual limit enforced.
4. **Issue a virtual card.** Until you do, the swipe and race panels are locked with a button
   to issue one. The full number is shown once; only the last 4 digits are stored.
5. **Swipe the card** using the merchant presets: Pharmacy, Hospital, Dentist and
   Eyeglasses are approved; Restaurant, Electronics and Grocery are declined. Each swipe
   prints a slip listing the five checks in order and which one failed.
6. **Freeze the card** and try another purchase (declined: card frozen). Unfreeze to resume.
7. **Race purchases against the lock:** e.g. amounts `10`, sent `20` times against a $100
   balance; exactly 10 are approved. If the amounts you enter fit inside the balance, the
   page says so and offers amounts that force a real race.
8. The **Ledger** lists every deposit and every purchase attempt; "Show checks" expands
   any purchase's decision trace.

## Run the tests

```bash
python -m unittest discover -s tests -t . -v      # 48 tests, ~7 seconds
python scripts/mutation_check.py                  # proves the race tests can catch races
```

Optional, a 29-step walkthrough in a headless browser (needs Playwright, which the app
itself doesn't):

```bash
pip install playwright && python -m playwright install chromium
python scripts/e2e_check.py
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

All endpoints take and return JSON. Requests send amounts as dollar strings (`"12.34"`);
responses return integer cents (`amount_cents`, `balance_cents`).

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

Status codes: `201` created (including **declined** purchases, which are a valid outcome,
not an error), `200` idempotent replay, `400` body isn't JSON, `404` unknown account or card,
`409` conflict (duplicate email, contribution limit, card already issued, reused idempotency
key), `422` invalid input, `503` busy (retry), `500` bug. Every error, crashes included, is
JSON: `{"error": {"code": "...", "message": "...", "field": "amount"}}`, where `field`
names the input at fault so the UI can show the message next to it.

Example with curl:

```bash
ACCT=$(curl -s localhost:8000/api/accounts -H 'Content-Type: application/json' \
  -d '{"holder_name":"Ada Lovelace","email":"ada@example.com","date_of_birth":"1985-12-10","coverage_type":"self_only"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -s localhost:8000/api/accounts/$ACCT/deposits -H 'Content-Type: application/json' -d '{"amount":"100"}'
CARD=$(curl -s -X POST localhost:8000/api/accounts/$ACCT/cards -H 'Content-Type: application/json' -d '{}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -s localhost:8000/api/transactions -H 'Content-Type: application/json' \
  -d "{\"card_id\":\"$CARD\",\"amount\":\"80\",\"merchant_name\":\"Corner Pharmacy\",\"mcc\":\"5912\"}"
```

## Project layout

```
app/
  api.py           HTTP routes; every error, crashes included, becomes JSON
  accounts.py      Accounts, deposits within the IRS limit, the activity feed
  cards.py         Issue, replace and freeze cards
  purchases.py     Authorization: the ordered CHECKS, the locked debit, the decision trace
  db.py            Connections, schema version check, write_transaction (BEGIN IMMEDIATE)
  schema.sql       Tables and the rules the database enforces itself
  merchants.py     MCC allow-list for qualified medical expenses
  limits.py        IRS annual contribution limits
  money.py         Dollar strings to integer cents, and back
  common.py        Clock, IDs, input helpers
  static/          index.html, app.js, styles.css, fonts/ (Public Sans, bundled; OFL)
tests/
  test_business_rules.py   One request at a time
  test_concurrency.py      Thread races, a random mixed workload, and a naive-code control
  test_http.py             API contract, error shapes, race through a real HTTP server
  test_db.py               Schema creation and refusing an old database
scripts/
  mutation_check.py        Breaks the concurrency control on purpose; the tests must notice
  e2e_check.py             Browser walkthrough (optional, needs Playwright)
```

## Demo video

<!-- TODO: paste your video link here -->
Video: _link_

An outline for recording it is in [docs/demo-video-outline.md](docs/demo-video-outline.md).

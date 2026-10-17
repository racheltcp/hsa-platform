# HSA Platform

A small Health Savings Account simulator built with Flask and SQLite.

The application lets a user create an account, contribute funds, issue and manage a virtual card, and authorize purchases while keeping account state correct under concurrent requests.

The HSA rules represented here are intentionally simplified for the take-home. This is a software engineering exercise, not a complete HSA compliance system.

**Backend:** Python 3.9+, Flask, SQLite (`sqlite3`, no ORM)  
**Frontend:** Plain HTML, CSS, and JavaScript served by Flask  
**Tests:** Python `unittest`, including multi-threaded concurrency tests and mutation checks

Documentation: [architecture.md](architecture.md) · [ai-usage.md](ai-usage.md) · [demo video](#demo-video)

---

## Run locally

```bash
git clone https://github.com/racheltcp/hsa-platform
cd hsa-platform

python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate

pip install -r requirements.txt
python run.py
```

Open:

```text
http://localhost:8000
```

Application data is stored in `data/hsa.db`, which is created automatically on first run.

To start over, stop the server and remove the `data/` directory.

Optional arguments:

```bash
python run.py --port 9000 --db /path/to/other.db
```

The application requires Python 3.9 or newer and SQLite 3.35+. `run.py` checks both at startup.

---

## Reviewer tour

The fastest place to start is **Run the $100 race** in the upper-right corner.

It creates a fresh account with a $100 balance, issues a card, and submits an $80 and $50 eligible purchase at approximately the same time.

Both purchases cannot safely succeed.

The timeline separates time spent waiting for the database lock, time spent holding the lock, and the final approved or declined result.

Which purchase wins can vary with scheduling. Either outcome is correct as long as only one purchase succeeds and the stored balance matches the successful transaction.

Other useful paths to try:

1. Create an account and enter a future date of birth or duplicate email to see validation.
2. Add contributions and try to exceed the annual limit represented by the simulator.
3. Issue a card, freeze it, attempt a purchase, and then unfreeze it.
4. Try eligible and ineligible merchant categories and inspect the decision trace.
5. Run a configurable race, such as twenty `$10` purchases against a `$100` balance.
6. Open **Activity** to see contributions, approved purchases, declined attempts, and their reasons.

---

## Key design choices

### Persisted state

The project uses SQLite rather than keeping application state only in memory.

The main persisted concepts are accounts, contributions, cards, and purchase attempts. This gives concurrent requests a shared source of truth and keeps state across process restarts.

### Money

Money is stored as integer cents instead of floating-point dollars.

For example:

```text
$12.34 -> 1234
```

This avoids relying on binary floating-point behavior for financial calculations.

### Contributions

Contributions are stored as individual records rather than only maintaining one accumulated total.

That preserves the history behind the annual total and gives the contribution-limit check persisted state to evaluate.

The contribution rules used here are intentionally simplified.

### Cards

Cards are separate from accounts because they have their own lifecycle.

A card can be active, frozen, cancelled, or replaced without replacing the account itself.

### Purchase attempts

Approved and declined purchase attempts are both persisted.

A decline does not move money, but the authorization still happened. Keeping the attempt lets the activity history show what was tried and why it stopped.

### Idempotency

Operations that may be retried support idempotency keys.

A timeout does not necessarily mean an operation failed. The server may have completed it while the response was lost. Recognizing the same logical request prevents a retry from becoming a duplicate purchase or contribution.

---

## Concurrency and correctness

The main concurrency problem this implementation protects against is making a decision using stale state.

Suppose an account has `$100`:

```text
Request A reads $100 and wants to spend $80
Request B reads $100 and wants to spend $50
```

If both requests approve using the same original balance, the system could approve `$130` against a `$100` account.

### `BEGIN IMMEDIATE`

Write workflows whose decisions depend on existing database state use:

```sql
BEGIN IMMEDIATE
```

This starts the SQLite write transaction before the application performs its read-decide-write sequence, preventing another writer from changing the database in the middle of that decision.

The contribution-limit flow is a good example:

```text
read existing contributions
-> decide whether another contribution is allowed
-> write the contribution
```

Protecting only the final SQL statement would not protect the earlier decision if another request changed the state between those steps.

### Conditional balance update

Purchases also use a conditional balance update.

The debit succeeds only if enough balance is still available when the update executes.

The transaction and the conditional update protect different parts of the operation. The transaction protects a larger decision based on previously read state. The conditional update protects the individual debit.

### Tradeoff

SQLite's writer lock is broad, so unrelated writes can still block one another.

For this project, that keeps the concurrency model small enough to inspect directly. For a larger production service, I would evaluate a database such as Postgres and more targeted concurrency control, including row-level locking.

See [architecture.md](architecture.md) for the full request flow, data model, and concurrency design.

---

## Verification

For higher-risk paths, the tests check the resulting state and invariants, not only the HTTP response.

For example, the card-replacement regression test does not stop at checking that invalid input returns `422`. It fetches the account afterward and verifies that the original card is still active.

The concurrency tests use the same idea. They check the resulting account state under competing requests rather than assuming that starting multiple threads is enough to prove the behavior is safe.

Run the full suite with:

```bash
python -m unittest discover -s tests -t . -v
```

The final suite has **49 passing tests** covering business rules, HTTP behavior, validation, persistence, idempotency, database behavior, and concurrent requests.

### Concurrency mutation check

Run:

```bash
python scripts/mutation_check.py
```

The mutation script copies the project to a temporary directory and deliberately weakens the concurrency protections.

A successful run looks like:

```text
[ok] baseline (no mutation)                                  -> tests pass
[ok] lock only: BEGIN IMMEDIATE + read-check-write in Python -> tests pass
[ok] BEGIN (deferred) instead of BEGIN IMMEDIATE             -> tests FAIL
[ok] atomic UPDATE only: no transaction lock                 -> tests FAIL
[ok] naive: no lock + read-check-write                       -> tests FAIL
```

This is not proof that every possible concurrency bug is covered. It is evidence that the suite detects several specific unsafe implementations.

Because the tests use real thread scheduling, the exact number of individual failures can vary between environments.

### Optional browser walkthrough

There is also an optional Playwright walkthrough:

```bash
pip install playwright
python -m playwright install chromium
python scripts/e2e_check.py
```

Playwright is only needed for this check and is not an application dependency.

---

## API

All endpoints take and return JSON.

Amounts are supplied as dollar strings:

```json
{"amount": "12.34"}
```

Stored monetary values use integer cents where applicable.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/accounts` | Create an account |
| `GET` | `/api/accounts` | List accounts |
| `GET` | `/api/accounts/{id}` | Get one account |
| `GET` | `/api/accounts/{id}/activity` | Get contributions and purchase attempts |
| `POST` | `/api/accounts/{id}/deposits` | Add a contribution |
| `POST` | `/api/accounts/{id}/cards` | Issue or replace a card |
| `POST` | `/api/cards/{id}/freeze` | Freeze a card |
| `POST` | `/api/cards/{id}/unfreeze` | Unfreeze a card |
| `POST` | `/api/transactions` | Authorize a purchase |
| `GET` | `/api/merchant-categories` | Return known merchant categories and eligibility |

### Card replacement

`POST /api/accounts/{id}/cards`

```json
{
  "replace": true
}
```

If `replace` is supplied, it must be an actual JSON boolean.

### Status codes

`200` Successful request or idempotent replay  
`201` Resource or purchase attempt created  
`400` Request body is not valid JSON  
`404` Account or card does not exist  
`409` Request conflicts with current state  
`422` Invalid input  
`503` Database is temporarily busy and the request may be retried  
`500` Unexpected application error

A declined purchase can still return `201` because the authorization attempt itself was successfully processed.

Errors use a consistent JSON shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Replace must be true or false.",
    "field": "replace"
  }
}
```

The `field` value lets the frontend associate validation feedback with the input that caused the error.

---

## Project layout

```text
app/
  api.py           HTTP routes, request parsing, and validation
  accounts.py      Accounts, contributions, limits, and activity
  cards.py         Card issuance, replacement, freezing, and unfreezing
  purchases.py     Purchase authorization, decision checks, and locked debit
  db.py            Connections, schema handling, and write transactions
  schema.sql       Persisted data model and database constraints
  merchants.py     Merchant-category eligibility used by the simulator
  limits.py        Contribution limits used by the simplified model
  money.py         Dollar strings to integer cents and back
  common.py        Shared IDs, time, and input helpers
  static/          HTML, CSS, and JavaScript

tests/
  test_business_rules.py   Single-request business rules
  test_concurrency.py      Concurrent requests, mixed workloads, and unsafe controls
  test_http.py             API behavior, validation, errors, and HTTP-level races
  test_db.py               Database initialization and schema handling

scripts/
  mutation_check.py        Deliberately weakens concurrency protections
  e2e_check.py             Optional browser walkthrough using Playwright
```

---

## AI usage

AI was used extensively during development.

See [ai-usage.md](ai-usage.md) for how I used Claude, where I questioned or changed generated work, and how I verified the final implementation.

---

## Demo video

[View demo video](https://drive.google.com/file/d/1F3A-BKOTk8_-cuIIecCchVJG76V4Yd9Q/view?usp=sharing)


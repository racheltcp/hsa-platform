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

1. Create an account and enter a future date of birth or duplicate email to see field-level validation.
2. Add contributions and try to exceed the annual limit represented by the simulator.
3. Issue a virtual card, freeze it, make a purchase, and then unfreeze it.
4. Make purchases from eligible and ineligible merchant categories. The result shows which checks ran and why the request was approved or declined.
5. Run a configurable race, for example twenty `$10` purchases against a `$100` balance. Exactly ten should be approved.
6. Open **Activity** to see contributions, approved purchases, declined attempts, and their decision traces.

---

## Design and data model

The project uses SQLite rather than keeping application state only in memory.

That gives concurrent requests a shared source of truth, keeps state across process restarts, and provides real transaction semantics while keeping the persistence layer small enough to inspect directly.

The main persisted concepts are accounts, contributions, cards, and purchase attempts.

### Accounts

An account contains the current balance and identifies the HSA owner.

Money is represented as **integer cents** rather than floating-point dollars.

For example:

```text
$12.34 -> 1234
```

Using integer cents avoids relying on binary floating-point behavior for financial calculations.

### Contributions

Contributions are stored as individual records instead of only maintaining one accumulated total.

That preserves the history behind the current contribution amount and gives the annual-limit check persisted state to evaluate.

The contribution rules in this simulator are intentionally simplified.

### Cards

Cards are modeled separately from accounts because they have their own lifecycle.

A card can be active, frozen, cancelled, or replaced without replacing the account itself.

The card-issuance endpoint validates `replace` as an actual JSON boolean.

Valid:

```json
{"replace": true}
```

Rejected:

```json
{"replace": "false"}
```

This distinction matters because a non-empty string is truthy in Python:

```python
bool("false") == True
```

Silently coercing that input could turn a malformed request into an unintended card replacement.

### Purchase attempts

Approved and declined purchase attempts are persisted.

A declined purchase does not move money, but it is still a decision the system made. Keeping the attempt allows the activity history to show what was attempted and why it stopped.

### Idempotency

Operations that may be retried support idempotency keys.

A timeout does not necessarily mean the original operation failed. The operation may have completed while the response was lost.

Recognizing the same logical request prevents that ambiguity from becoming a duplicate purchase or contribution.

---

## Concurrency and correctness

The main concurrency failure this implementation protects against is making a decision using stale state.

Suppose an account has `$100`:

```text
Request A reads $100 and wants to spend $80
Request B reads $100 and wants to spend $50
```

If both requests approve based on the same original balance, the system could approve `$130` against a `$100` account.

### `BEGIN IMMEDIATE`

Write workflows whose decisions depend on existing database state use:

```sql
BEGIN IMMEDIATE
```

This starts the SQLite write transaction before the application performs its read-decide-write sequence, so another writer cannot change the database in the middle of that decision.

The contribution-limit flow is an example:

```text
read existing contributions
-> decide whether another contribution is allowed
-> write the contribution
```

Protecting only the final SQL statement would not protect the earlier decision if another request could change the underlying state between those steps.

### Conditional balance update

Purchases also use a conditional balance update.

The debit only succeeds if enough balance is still available when the update executes.

The transaction and conditional update therefore protect different parts of the operation. The transaction protects the larger decision based on previously read state, while the conditional update protects the individual debit.

### Tradeoff

SQLite's writer lock is broad, so unrelated write operations can still block one another.

For this take-home, that keeps the persistence and transaction model simple enough to inspect directly.

For a larger production service using a database such as Postgres, more targeted concurrency control such as row-level locking would be worth evaluating rather than carrying the SQLite approach over unchanged.

See [architecture.md](architecture.md) for more detail.

---

## API validation

The API rejects invalid representations instead of silently guessing what the caller intended.

One example is the `replace` value when issuing a card.

The initial implementation passed the value through Python's `bool()` function, which means:

```python
bool("false")   # True
```

That could turn malformed input into a destructive state change by replacing an active card.

The endpoint now accepts only an actual JSON boolean. Invalid values return `422`.

The regression test checks more than the response status. After the rejected request, it fetches the account again and verifies that the original card is still active. It then verifies that an actual JSON `true` still performs a valid replacement.

---

## Verification

For the higher-risk paths, the goal of the tests is not only to exercise an endpoint. I want them to check the state or invariant the application is claiming to protect.

The card-replacement test is one example. A `422` response alone would not tell me whether the existing card had already been changed, so the test reads the account afterward and verifies that the original card is still active.

The concurrency tests take the same approach. They check the resulting account state under competing requests rather than assuming that starting multiple threads is enough to prove the behavior is safe.

Run the complete suite with:

```bash
python -m unittest discover -s tests -t . -v
```

The suite covers business rules, HTTP behavior, validation, persistence, idempotency, database behavior, and concurrent requests.

### Concurrency mutation check

Run:

```bash
python scripts/mutation_check.py
```

The mutation script copies the project to a temporary directory and deliberately weakens the concurrency protections.

A successful run looks like:

```text
[ok] baseline (no mutation)                                     -> tests pass
[ok] lock only: BEGIN IMMEDIATE + read-check-write in Python    -> tests pass
[ok] BEGIN (deferred) instead of BEGIN IMMEDIATE                -> tests FAIL
[ok] atomic UPDATE only: no transaction lock                    -> tests FAIL
[ok] naive: no lock + read-check-write                          -> tests FAIL
```

This is not intended as proof that every possible concurrency failure is covered.

It provides evidence that the test suite is sensitive to several specific unsafe implementations rather than only passing against the final version.

Because these tests use real thread scheduling, the exact number of individual failures may vary between environments. The important result is that the intentionally unsafe variants are detected.

### Optional browser walkthrough

There is also an optional end-to-end browser walkthrough using Playwright:

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

**200** - Successful request or idempotent replay  
**201** - A resource or purchase attempt was created  
**400** - Request body is not valid JSON  
**404** - Account or card does not exist  
**409** - Request conflicts with current state  
**422** - Invalid input  
**503** - Database is temporarily busy and the request may be retried  
**500** - Unexpected application error

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
  static/          HTML, CSS, JavaScript, and bundled fonts

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

See [ai-usage.md](ai-usage.md) for how I reviewed the generated implementation, what I personally ran and changed, and what I verified before submission.

---

## Demo video

Video: https://drive.google.com/file/d/1F3A-BKOTk8_-cuIIecCchVJG76V4Yd9Q/view?usp=sharing


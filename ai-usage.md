# AI usage

> **TODO before submitting:** this file was drafted from the actual build session. Sections
> marked *TODO* need your own words: what you personally reviewed, changed, and re-ran.
> Delete this note when done.

## Tools used

| Tool | Used for |
| --- | --- |
| **Claude (Opus 5.5, claude.ai with code execution)** | Design, implementation, tests, docs, and running verification (test suite, mutation check, headless-browser walkthrough) in a sandbox |
| *TODO: anything else you used (IDE assistant, ChatGPT, etc.), or delete this row* | |

## Which parts AI helped with

AI generated the first version of essentially every file: schema, service layer, Flask routes,
UI, tests and documentation. My role was to set constraints, interrogate the design, and,
most importantly, decide what evidence would convince me it was correct, rather than
accepting "all tests pass" at face value.

| Area | AI contribution | My contribution |
| --- | --- | --- |
| Data model | Proposed integer cents, stored balance + ledger rows, DB-level CHECKs, partial unique index for one live card | *TODO: what you questioned or changed* |
| Concurrency | Proposed `BEGIN IMMEDIATE` + conditional `UPDATE` | Required proof the tests can detect a race; that surfaced a wrong claim (below) |
| Domain rules | MCC allow-list; IRS contribution limits | Verified 2026 limits ($4,400 / $8,750) against current IRS-based reporting rather than model memory |
| UI | Single-page vanilla JS | Reviewed screenshots; found a stale-state bug |
| Docs | Drafted README / architecture / this file | *TODO: your edits* |

## Representative prompts and workflows

**1. Build from the brief.** I attached the assignment PDF and asked Claude to build the
project. It planned the stack, discovered the sandbox could not reach PyPI (so FastAPI was
unavailable), and switched to Flask + stdlib `sqlite3` + `unittest`, which turned out
better for the reviewer: one dependency, no build step.

**2. "The tests all passed on the first run. Prove they can fail."** A concurrency test
that passes against correct code proves nothing if it would *also* pass against broken
code (for example, if the threads never actually overlap). So the workflow was:
- add a **control test** that runs a deliberately naive read-check-write purchase under
  the same thread/barrier harness and asserts that it *does* double-spend;
- write `scripts/mutation_check.py`, which copies the project, removes each concurrency
  safeguard in turn, and confirms the race tests fail for every unsafe variant.

**3. Click through the UI like a reviewer.** Claude drove the running app in a headless
Chromium (Playwright): created an account, deposited, issued a card, made qualified and
non-qualified purchases, froze the card, exceeded the contribution limit, ran the race
scenario, tried an XSS payload as an account name, and checked a 390px mobile viewport.
I reviewed the screenshots and the printed results.

*TODO: add a prompt of your own, e.g. a follow-up question you asked to understand a piece of the design.*

## Where AI output was wrong, and how it was corrected

### The main one: an overconfident claim about what makes the system safe

The first version of `process_transaction` had this docstring:

> The balance debit is additionally a single conditional UPDATE ... so the "check funds"
> and "take funds" steps ... cannot be interleaved even if the surrounding transaction
> were weakened later.

That implies the system stays safe if the transaction lock is ever weakened, as long as
the atomic UPDATE remains. The mutation check showed that was **wrong**. With the lock
removed and the conditional UPDATE kept:

- purchases were indeed still safe, **but**
- 20 concurrent $300 deposits were **all accepted**: $6,000 against a $4,400 annual limit,
  because "sum this year's deposits, then insert" spans multiple rows and needs the lock;
- concurrent retries with the same idempotency key crashed with a `UNIQUE` constraint
  error (HTTP 500) instead of returning the original result.

So the lock is the load-bearing guarantee, and the conditional UPDATE is defense in
depth, not a substitute for it. I corrected the docstring and wrote up the
measured results as a table in `architecture.md`. This is the most useful lesson of the
project for me: the code was correct, but the *explanation* of why it was correct was not,
and that would have been exposed the moment an interviewer asked "what if you removed X?"

### Smaller corrections

- **Floats in money formatting.** Storage and arithmetic were integer cents, but the
  first display helper did `f"{cents / 100:.2f}"`. Replaced with integer-only `divmod`
  formatting and a test, so no float touches a money value anywhere.
- **Stale UI state.** Reviewing a screenshot showed the "Run the race" button switched to
  a new account but still displayed the previous account's last purchase result and
  "repeat 20" setting. Fixed by resetting per-account UI state in one `selectAccount()`
  function.
- **Didn't run on a stock Mac.** The code used `str | None` type hints, which need Python
  3.10+, and was only tested on 3.13. The first run on my MacBook, which ships Python 3.9.6,
  crashed at import. Fixed with `from __future__ import annotations`. The full test suite
  and mutation check were then re-run on an actual Python 3.9 interpreter, and `run.py`
  now checks the Python and SQLite versions up front with a clear message. The same session
  switched the default port from 5000 to 8000, because macOS's AirPlay Receiver occupies 5000.
- **Unformatted error message.** The limit error read "$4400.00"; changed to "$4,400.00".
- **Unverifiable citation.** An early draft of `limits.py` cited specific IRS Revenue
  Procedure numbers from memory. Since I could not verify those numbers, the citation
  was replaced with a plain description; the dollar figures themselves were checked
  against a web search.

*TODO: if you found anything yourself while reviewing, it belongs here and is worth more than the items above.*

## How I verified the final implementation

1. **Automated tests:** `python -m unittest discover -s tests -t .` runs 39 tests:
   business rules, API contract, and concurrency (threads released together by a barrier,
   50 rounds of the $100 / $80 + $50 scenario, 40 x $10 against $100, concurrent deposits
   against the limit, concurrent idempotent retries, and 30 simultaneous HTTP requests
   against a real threaded server). Every concurrency test also checks the ledger
   invariant `balance == deposits - approved purchases`.
2. **Tests of the tests:** the naive-implementation control test, plus
   `python scripts/mutation_check.py` showing the suite fails when any safeguard that
   matters is removed.
3. **Database-level invariants:** a test confirms a direct `UPDATE ... SET balance_cents = -1`
   is rejected by the schema, independent of application code.
4. **End-to-end UI walkthrough** in a headless browser, including an XSS probe (account
   name `<img src=x onerror=alert(1)>` renders as text) and a mobile-width check.
5. **Domain facts** (2026 HSA limits) checked against current sources instead of trusting
   model memory.
6. *TODO: what you ran and read yourself — e.g. "I read every line of services.py and db.py,
   re-ran the test suite and mutation check on my machine (OS / Python version), and walked
   through the reviewer tour in the README."*

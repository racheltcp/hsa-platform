# AI usage

> **TODO before submitting:** drafted by Claude from the actual build conversation, with
> each action credited to whoever really did it. The *TODO* items need your own words:
> what you personally read, questioned, changed and re-ran. Delete this note when done.

## Tools used

| Tool | Used for |
| --- | --- |
| **Claude (Opus 5.5, in claude.ai with code execution)** | Design, all code, tests and docs, and running the checks (test suite, mutation check, browser walkthrough) in its sandbox |
| *TODO: anything else you used, or delete this row* | |

## Who did what

Being plain about this: Claude wrote essentially every file. My part was directing it,
running the project on my own machine, pushing back when results weren't good enough, and
deciding what had to be proven before I'd submit it.

| | Claude | Me |
| --- | --- | --- |
| Build | Chose the stack, wrote the backend, UI, tests and docs | Gave it the assignment and asked it to build the project |
| Running locally | Tested on Python 3.13 in its sandbox | Ran it on my MacBook, hit a crash at startup, and reported it (below) |
| Concurrency proof | Added a naive-code control test and the mutation check on its own initiative | *TODO: whether you've run `python scripts/mutation_check.py` yourself* |
| UI | Built two versions; ran browser walkthroughs and reviewed its own screenshots | Said the first version looked generic; sent screenshots from my machine |
| Review | Did a critical review on request; found and fixed the bugs listed below | Asked for a critical, ranked review and said not to settle for "looks good" |
| Domain facts | Looked up the 2026 IRS limits with a web search | *TODO: if you checked anything yourself* |

## Representative prompts and workflows

**1. Build it from the brief.** I attached the assignment PDF and asked Claude to do the
project. Its sandbox couldn't reach PyPI, so it switched from FastAPI to Flask, which was
already installed, plus the standard library's `sqlite3` and `unittest`. That turned out
well for reviewers: one dependency and no build step.

**2. Prove the tests can fail.** All the tests passed on the first run, and Claude treated
that as suspicious rather than reassuring. A concurrency test proves nothing if it would
also pass against broken code, for example if the threads never overlap. So it added:
- a control test that runs a deliberately naive purchase under the same harness and checks
  that it *does* double-spend;
- `scripts/mutation_check.py`, which removes each safety mechanism in a copy of the code
  and confirms the concurrency tests fail.

**3. "It looks generic. Will it impress anyone?"** I pushed back on the first UI. Claude
agreed it was a template-style dashboard and rebuilt it around what the system actually
does: lock timings measured on the server, drawn as a timeline, and a printed decision trace
for every purchase. One fix came from a screenshot I sent. I'd raced $80 and $50 against
$198.57, both were approved, and the "race" showed nothing. The panel now warns when the
amounts can't compete and offers amounts that will.

**4. "Be critical. Find what I missed."** I asked for a ranked review of the whole project
covering correctness, concurrency, idempotency, tests and UX, with real fixes rather than
cosmetic ones. The bugs it found are below.

*TODO: add a prompt of your own, such as a question you asked to understand part of the design.*

## Where the AI's output was wrong, and how it was caught

### 1. A wrong explanation of why the system was safe

The first version of the purchase code said the conditional `UPDATE` keeps the balance safe
"even if the surrounding transaction were weakened later". In other words, the lock was
optional. The mutation check disproved it. With the lock removed:
- purchases were still safe, but
- 20 simultaneous $300 deposits were all accepted, $6,000 against a $4,400 limit;
- simultaneous retries with the same key crashed instead of returning the original result.

The lock is the essential guarantee; the conditional `UPDATE` is a backstop. The code was
right but its explanation was wrong, which is exactly what an interviewer's "what if you
removed X?" would expose.

### 2. Bugs found in the critical review (all now fixed, each with a regression test)

| Problem | How it showed up | Fix |
| --- | --- | --- |
| The check order was written in two places: once for the decision, once for the explanation | Code reading. Nothing kept them in sync | One ordered `CHECKS` list that both read; a test makes each check fail alone |
| An amount of `1e999999999` crashed the server with an HTML 500 | Probing the API | Bounds checked before any arithmetic. The first fix then let `1e-999999999` through as 0 cents, caught by the edge-value probe and fixed |
| Retrying a purchase with a different merchant name returned the old result | Probing the API | Retries must match amount, category *and* merchant |
| Retry keys were global, so account B got a 409 for a key account A had used | Probing the API | Keys scoped per card and per account in the schema |
| Any crash, or a busy database, returned an HTML page the UI couldn't read | Probing the API | Every error is JSON with a readable message; busy is a 503 |
| Malformed JSON was reported as "holder_name is required" | Probing the API | Its own 400 error |
| An account "closed" state that nothing could ever reach | Code reading | Removed, with its dead decline branch |
| The "threads really overlapped" test only asserted a wait of more than 0 ms | Code reading | Now asserts most requests queued behind another lock holder |
| Timings used the wall clock, which can jump | Code reading | Monotonic `perf_counter` |
| The "Use $60 and $38 instead" button sometimes ignored clicks | Found by the browser walkthrough | Redraw the hint only when its text changes |
| Error messages used code names ("date_of_birth must be…") and vanished in a toast | UX review | Plain-language messages shown next to the field |

### 3. Problems found earlier

- **Didn't run on a stock Mac.** Code that needed Python 3.10 crashed at startup on my
  MacBook's Python 3.9.6. Claude had only tested on 3.13. Fixed, and it now reruns
  everything on a real 3.9 interpreter. The default port also moved from 5000 to 8000,
  because macOS's AirPlay Receiver uses 5000.
- **Floats in money formatting.** Display used `cents / 100`; replaced with integer-only formatting.
- **Stale screen state** after switching accounts, found in Claude's own screenshots.
- **An unverifiable citation.** An early draft cited IRS document numbers from memory; it
  was replaced with a plain description, and the dollar figures were checked by web search.
- **Fonts from Google** were blocked in the sandbox and wouldn't load offline; the font is now bundled.

*TODO: anything you found yourself belongs here, and it's worth more than everything above.*

## How the final implementation was verified

What Claude ran, in its sandbox, after the last change:
1. `python -m unittest discover -s tests -t .`: 48 tests passing, on Python 3.13 and on 3.9.
   The concurrency tests were repeated 10 times on 3.13 and 5 times on 3.9 to check for
   flakiness. They cover the brief's race 50 times, 40 purchases at once, a lock-timing
   proof, a random mix of 90 operations across 3 accounts, and 30 simultaneous HTTP requests.
2. `python scripts/mutation_check.py`: the tests fail for every unsafe variant.
3. `python scripts/e2e_check.py`: 29 browser checks covering every flow, each error message,
   desktop and phone widths, an XSS attempt, and the server being stopped mid-session.
4. An old database from the previous version: the app refuses it with a clear message.

What I verified myself:
- I ran the app on my MacBook (Python 3.9.6) and used it through the browser.
- *TODO: list what you actually did, e.g. "Read purchases.py and db.py line by line; ran the
  test suite, mutation check and e2e check on my Mac; worked through the review questions."*

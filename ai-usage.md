# AI Usage

I used Claude heavily while building this project. It generated most of the initial implementation, tests, and documentation.

Since AI use was expected for the assignment, I did not try to minimize how much I used it. What mattered more to me was whether I actually understood the important parts of what it generated and whether I had evidence that the system behaved the way I was claiming.

That became the main way I reviewed the project. If something passed, I wanted to know what the test was actually checking. If the UI showed a concurrency state, I wanted to know whether that matched what the backend was doing. If an invalid request returned an error, I also wanted to know whether it had changed any state before failing.

## What Claude helped with

Claude generated most of the initial Flask API, SQLite schema and persistence layer, account and contribution flows, virtual card behavior, purchase authorization, merchant eligibility logic, idempotency handling, concurrency handling, frontend, automated tests, and the first versions of the README and architecture documentation.

It also proposed several of the design choices that remained in the final version, including storing money as integer cents, using idempotency keys, wrapping write workflows in `BEGIN IMMEDIATE`, using a conditional balance update for purchases, and adding concurrency tests.

I did not personally come up with all of those choices, so I did not want to present them as if I had. My goal during review was to understand why they were there, where they were actually useful, and where the implementation or explanation still needed work.

## Running the project outside Claude's environment

One of the first useful checks was simply running the project on my own machine.

Claude had been working in a newer Python environment, while my Mac was using Python 3.9.6. The application crashed because some of the generated type-annotation syntax was not compatible with that version.

I did not diagnose the compatibility issue myself. I found it by running the application locally, sent the failure back to Claude, applied the compatibility changes, and tried again.

That changed how I approached the rest of the project. I did not want Claude's own environment to be the only place verifying code that Claude had generated.

It also made me pay more attention to the difference between "this worked once" and "I have checked the thing I am actually relying on."

## Challenging a passing test suite

At one point the project already had 42 passing tests.

Instead of asking Claude to keep adding features, I asked for another review with a different goal. One of the prompts I gave was essentially:

> Don't assume the code is correct just because the existing tests pass. Review transaction correctness, concurrency, idempotency, database consistency, and whether the tests actually support the claims we are making.

I was less interested in increasing the number of tests than in whether the existing tests would notice if an important protection were broken.

That review found several problems I had not found myself. I do not want to take credit for the individual discoveries. What I did do was push for the implementation to be challenged instead of simply validated, work through the results, and rerun the project after changes.

The concurrency path was the part I spent the most time trying to understand afterward.

The easiest example is an account with $100 and two purchases arriving around the same time, one for $80 and one for $50. If both requests independently read the original $100 and then make their decisions from that same state, they could both think there is enough money even though the account cannot cover $130.

For purchases, the implementation has a conditional balance update. The debit succeeds only if enough money is still available when the update executes.

That protects the individual debit, but I initially did not understand why that was not enough by itself.

The contribution path made the difference clearer to me. A contribution has to read how much has already been contributed, compare that total with the annual limit, decide whether another contribution is allowed, and then write the result.

If another request can change the database between those steps, every SQL statement can be valid by itself while the overall decision is still based on stale information.

That is where `BEGIN IMMEDIATE` matters. It starts the SQLite write transaction before the application enters that read-decide-write sequence, so another writer cannot change the database in the middle of the decision.

I now think of the transaction and the conditional debit as related protections that solve different problems. The conditional update protects an individual balance change. The transaction protects a larger decision that depends on state that was read earlier.

## Where Claude's explanation was incomplete

One of the concurrency explanations was too broad about what the conditional purchase update could protect.

It made the atomic balance update sound closer to a replacement for the transaction lock than it really was.

The mutation testing helped expose that difference.

When the transaction protection was weakened, the purchase path still had protection against directly overdrawing a balance because of the conditional update. But other workflows that depended on reading state before making a decision were no longer protected in the same way.

The contribution path was the clearest example. Multiple requests could each make a decision using an outdated contribution total and collectively violate the annual limit.

That made me go back and revise how I understood the design. The conditional update is useful defense for purchases, but it does not replace transaction-level protection for every invariant in the application.

I also made sure the documentation did not describe those two safeguards as if they were interchangeable.

## Checking whether the tests notice when protections disappear

Claude had already created a mutation-checking script, so mutation testing itself was not my idea.

What I found useful was the question it let me ask. Instead of only running the correct implementation and seeing green tests, the script intentionally weakens specific concurrency protections and checks whether the tests stop passing.

I ran the mutation check locally.

The intentionally unsafe versions did not always fail in exactly the same way or with the exact same number of failures on my Mac as they had in Claude's environment. That made sense to me because thread scheduling is timing-dependent.

I was not trying to reproduce an exact failure count. What mattered was whether removing an important protection caused the relevant tests to detect a problem.

I also would not treat the mutation check as proof that the concurrency implementation is perfect. It shows that the test suite is sensitive to several specific unsafe alternatives. There could still be failure modes those mutations do not represent.

That distinction mattered to me because I did not want the documentation or demo to claim more than the evidence actually showed.

## Reviewing state-changing behavior before submission

Near the end, I asked Claude for another review rather than adding more features.

I wanted that pass to focus on correctness risks, documentation accuracy, TODOs, unused or debugging code, and anything important that I should understand before submitting.

During that review, another issue came up in the card-issuance endpoint.

The endpoint was converting the incoming `replace` value using Python's `bool()` behavior. A non-empty string is truthy in Python, so a value like `"false"` could still be interpreted as true.

The part that mattered to me was not just that the input type was technically wrong. This endpoint can cancel a customer's active card and issue a replacement.

I changed the route so `replace` must be an actual JSON boolean. If the field is missing, it defaults to `false`. If the caller sends a string instead, the request returns `422`.

I also added a regression test.

I did not want that test to stop at checking the HTTP status code. A `422` would not be enough if the request had already changed the customer's card before failing.

After sending the invalid request, the test fetches the account again and verifies that the original card is still active. It then sends an actual JSON `true` and checks that a valid replacement still works.

For me, that was a better test because it checks the state I actually care about protecting.

## Making sure the UI matched the backend

I pushed back on the generated UI several times too.

The first dashboard worked, but I thought it still felt like a collection of separate boxes rather than one account. I wanted the balance, contributions, card, and activity to feel connected.

The concurrency visualization was where I cared more about accuracy than appearance.

I asked Claude to distinguish between a request waiting for the database lock, a request actually holding the lock, and the final approved or declined result. Those are not the same state.

I specifically asked Claude not to make the visualization cleaner at the cost of making the concurrency behavior less accurate.

That feedback led to another issue being found in how the timeline represented those states. Claude found the specific implementation problem, but I kept pushing on that area because I did not want the demo to visually claim something that the backend was not actually doing.

I also tested parts of the UI myself in the browser. For example, after the date-of-birth validation changed, I checked that the error appeared directly below the field and cleared after the date changed.

## Understanding idempotency

I also went back through the idempotency path because it was easy to see the code without immediately understanding the customer problem it was solving.

A timeout does not tell a caller whether an operation happened. The server may have completed a purchase or contribution and the response may simply have been lost.

Without idempotency, retrying that request could create a second transaction even though the caller only intended one.

The idempotency key gives the application a way to recognize that it has already handled that logical operation and return the earlier result instead of performing it again.

That is the kind of behavior I would care about even more in a real financial system because a retry should not become a second charge or contribution.

## How I verified the final implementation

I ran the full test suite locally on my Mac:

```bash
python -m unittest discover -s tests -t . -v
```

The final suite has **49 passing tests**.

I also ran:

```bash
python scripts/mutation_check.py
```

The baseline implementation and the normal `BEGIN IMMEDIATE` implementation passed. Changing the transaction to deferred locking caused the tests to fail. Removing the transaction lock and relying only on the atomic update also caused tests to fail, and the naive read-check-write implementation failed as well.

I do not see that as proof that no concurrency bug exists. I see it as evidence that the tests detect those particular broken versions.

I also ran the application locally and exercised the main flows in the browser, including purchases and the concurrency race.

Before submitting, I reran the full suite and mutation check after the final API change.

## What I would revisit before production

I would not call this production-ready just because the take-home works.

The first area I would revisit is the HSA domain itself. The application intentionally uses simplified HSA rules. I can test whether the software follows the rules we implemented, but I would not want to independently decide that those rules are complete or correct for a real financial product.

That is an area where I would want someone with deeper HSA, compliance, or regulatory knowledge involved before treating the assumptions as production requirements.

I see the database and locking strategy a little differently.

SQLite's writer lock is broad, so unrelated accounts can block one another. For this project, that makes the concurrency model relatively simple to inspect.

At a larger scale, I would investigate a database such as Postgres and more targeted locking, including row-level locking. I would first want to understand the tradeoffs and failure modes myself, then bring options to the team rather than assuming the SQLite design should carry over unchanged.

I would also want better production observability around retries, lock contention, unexpected declines, and failures that are difficult to reproduce locally.

Before submitting, I wanted to make sure I could explain why the important protections were there, what the tests were actually checking, and which parts I would still want more information or expertise on before making a production decision.

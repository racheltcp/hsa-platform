# AI Usage

I used Claude heavily while building this project. It generated most of the initial implementation, tests, and documentation.

Since AI use was expected for the assignment, the part I spent the most time on was figuring out what I could actually trust.

I kept coming back to the same question: if the project is making a claim, what evidence do I have that the claim is true?

It was not enough for the project to work only in Claude's environment. A green test suite was not enough if the tests never exercised the failure case. A concurrency demo was not useful if the visualization did not match what the backend was actually doing. And an error response was not enough if the rejected request could still leave the database in the wrong state.

That shaped how I reviewed the rest of the project.

## Running it outside Claude's environment

One of the first problems showed up when I tried to run the project on my Mac.

Claude had tested it using a newer Python version, while I was using Python 3.9.6. The app crashed because some of the generated type syntax was not compatible with my environment.

I did not diagnose that compatibility issue myself, but I surfaced it by actually running the project outside Claude's environment. I sent the failure back, applied the fix, and ran it again locally.

That was an early example of why I did not want the environment that generated the code to be the only environment verifying it.

## Understanding the system

I also did not want to treat the generated implementation as a black box.

The project is a Flask application backed by SQLite. The main pieces are separated by responsibility: accounts and contributions, cards, purchases, database and transaction handling, merchant eligibility, money parsing, and the HTTP layer.

I went through those pieces so I could follow an operation from the HTTP request to the state that was actually persisted.

Accounts hold the current balance and identify the owner. Contributions are stored as individual records instead of only keeping an accumulated number, so the history behind the annual total is still available.

Cards are separate because they have their own lifecycle. A card can be active, frozen, cancelled, or replaced without replacing the account.

Purchase attempts are persisted even when they are declined. No money moves on a decline, but the authorization still happened. Keeping it means the activity history can show what was attempted and why it stopped.

Money is stored as integer cents instead of floating-point dollars so financial calculations do not depend on binary floating-point behavior.

I also spent time understanding the idempotency path.

A timeout does not tell the caller whether the operation happened. The server might have completed it and the response might simply have been lost.

Without idempotency, retrying that request could create a second purchase or contribution. The key gives the application a way to recognize that it has already handled the same logical operation.

## Challenging a green test suite

The area I pushed on the hardest was correctness.

At one point the project had 42 passing tests. I specifically told Claude not to assume that meant the implementation was correct.

I asked it to take another look at transaction correctness, concurrency, idempotency, database consistency, and whether the tests actually supported the claims we were making about the system.

A test can be green without ever reaching the failure case that matters.

That deeper review found several problems I had not found myself. I do not want to take credit for those individual discoveries.

What I did do was ask for the implementation to be challenged instead of simply validated, work through what came back, and rerun the project and tests instead of treating the fix as the end of the investigation.

## Understanding the concurrency path

Claude chose the original locking strategy, including `BEGIN IMMEDIATE` and the conditional balance update.

I went back through that part of the code because I wanted to understand what each protection was actually doing.

The simplest race is an account with $100 and two purchases arriving around the same time, one for $80 and one for $50.

If both requests read the original $100 and make their decisions independently, both could approve even though the account cannot cover $130.

For a purchase, the balance update is conditional. The debit only succeeds if enough money is still available when the update executes. That protects the individual balance change.

The contribution path helped me understand why an atomic update is not enough for every invariant.

That operation has to read how much has already been contributed, decide whether another contribution is allowed, and then write it.

If another writer can change the state between those steps, every SQL statement could be valid on its own while the overall decision is still based on stale information.

My understanding of `BEGIN IMMEDIATE` is that SQLite starts the write transaction before that read-decide-write sequence begins, so another writer cannot change the database in the middle of the decision.

I think of that transaction and the conditional balance update as related protections, but not identical ones. The conditional update protects an individual debit. The transaction protects a larger decision that depends on state that was read earlier.

There is a tradeoff.

SQLite's writer lock is broad, so unrelated accounts can still block one another. For this project, that keeps the concurrency model relatively simple to inspect. At a larger scale on something like Postgres, I would want to investigate more targeted locking such as row-level locks instead of assuming this design should carry over unchanged.

## Checking whether the tests notice when protection disappears

I also ran the concurrency mutation check myself.

Claude had already created the mutation testing, so that was not my idea.

What I found useful about it was that it changed the question.

Instead of only asking whether the final implementation passes, it deliberately weakens specific concurrency protections and asks whether the tests notice.

When I ran it locally, the intentionally naive concurrency version did not fail exactly the same number of tests on my Mac as it did in Claude's environment.

That made sense because thread scheduling is timing-dependent.

I was not trying to reproduce an exact failure count. The result I cared about was that weakening the protections caused the relevant tests to stop passing.

I also would not call that proof that the implementation is correct. It is evidence that the suite detects several specific unsafe alternatives. There could still be failure modes those mutations do not represent.

## A final API change

During final review, Claude pointed out another issue in the card-issuance route.

The endpoint was converting the incoming `replace` value with Python's `bool()` function.

I went through the behavior and realized that a non-empty string is treated as `True` in Python, even when the text inside that string says false.

For this endpoint, that has a real consequence. If a client sends false as a string instead of an actual JSON boolean, the server could interpret it as permission to cancel the customer's current card and issue a replacement.

I did not want the server guessing in a path with that kind of side effect.

I changed the route in my local repository so `replace` only accepts an actual JSON boolean. If the field is missing, it defaults to `false`. If the caller sends a string instead of a real boolean, the request now returns `422`.

I also added the regression test.

I did not stop at checking the status code.

After sending the invalid request, the test fetches the account again and checks that the original card is still active. It then sends an actual JSON `true` and verifies that valid replacement still works.

The important part was making sure the rejected request could not change the customer's card.

After the change, I reran the full test suite and mutation check locally.

## Making sure the UI did not overstate the backend

I pushed back on the generated UI several times too.

The first dashboard technically worked, but it still looked like separate white boxes instead of one account. I wanted the balance, contributions, card, and activity to feel connected.

The concurrency visualization was the part where I cared most about accuracy.

I specifically asked for it to distinguish between a request waiting for the lock, actually holding the lock, and its final approved or declined result.

Those are different states. A request can be waiting without owning the lock yet, and its eventual transaction result is separate again.

I explicitly told Claude not to prioritize visual appearance over accurately representing the concurrency behavior.

That feedback led to another problem being found in how the timeline represented those states. Claude found the specific implementation issue, but the reason I kept pushing on it was that I did not want the demo to visually claim behavior that the backend was not actually providing.

I also tested the UI directly in my browser.

One example was the date-of-birth validation. After that interaction changed, I checked that the error appeared below the field and cleared after the date changed.

## Production considerations

I would not call this production-ready just because the take-home works.

The HSA rules are simplified. In a real financial product, I would want the actual regulatory requirements and edge cases reviewed with people who know that domain before treating my assumptions as authoritative.

I would also revisit the SQLite locking strategy at production scale rather than assuming the same approach should carry over unchanged.

The tests and mutation check give me evidence for the cases they cover, but in production I would still want observability around retries, lock contention, unexpected declines, and failures that are difficult to reproduce locally.

Those are areas where I would want more context before making a production decision.

## Final review

Before submitting, I reran the full test suite and mutation check and went back through the parts of the code I thought carried the most risk.

The areas I paid the most attention to were concurrency, idempotency, input validation, and whether the tests were actually checking the state the application claimed to protect.

I also made sure I could follow the important paths through the code and explain what each protection was doing instead of only knowing that the generated implementation passed its tests.

There are still things I would revisit in a production system, especially the simplified HSA rules, SQLite locking at a larger scale, and production observability around retries and lock contention.

For this take-home, the main thing I wanted was enough evidence behind the important claims that I could explain why I trusted them and where I still would not.
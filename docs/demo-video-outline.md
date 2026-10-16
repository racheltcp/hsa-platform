# Demo video outline (target: 4:30, hard limit 5:00)

Record with the app at `http://localhost:8000` on a fresh database (`rm -rf data/` first),
`architecture.md` open in a second tab, and a terminal ready.

| Time | Section | What to show / say |
| --- | --- | --- |
| 0:00–0:20 | Intro | One sentence on what an HSA is; one sentence on the stack (Flask, SQLite, vanilla JS, no build step). |
| 0:20–1:40 | **1. Demo** | Create an account (point out DOB + coverage type drive the IRS limit). Deposit $100. Issue a card (full number shown once, only last 4 stored). Pay at Pharmacy: approved. Pay at Restaurant (the tile already says "Not eligible"): the receipt's first line is the reason, and the step strip shows check 4 failed and check 5 (funds) never ran. Freeze card, swipe: declined. Try depositing $5,000: limit error. |
| 1:40–2:10 | **The race** | Click "Run the $100 race". Walk the timeline: amber is waiting, navy is holding the lock, the dot is the decision. The $50 one waits while the $80 one holds the lock, then "saw only $20.00" and is declined. Click again: sometimes $50 wins; both are correct. Then send `10` x 30: the staircase shows strict one-at-a-time access, and the gaps are SQLite's busy-handler backoff (a good "what I'd improve" hook). |
| 2:10–2:40 | **2. Architecture** | Show the layer diagram: UI → thin Flask routes → framework-free service layer → SQLite. The service layer takes a plain connection, which is why tests can drive it from 40 threads. |
| 2:40–3:05 | **3. Data model** | ER diagram. Integer cents. Stored balance plus ledger rows with the invariant tested. Declines recorded. DB-level CHECKs and the partial unique index for one live card. |
| 3:05–3:50 | **4. Transaction processing** | Open `app/purchases.py`: the five `CHECKS` in one list, then `authorize_purchase`: BEGIN IMMEDIATE → idempotency → checks 1–4 → conditional UPDATE (check 5) → insert → commit. Explain the check-then-act race and why the naive version loses money even though the balance stays positive. Run `python scripts/mutation_check.py` and point at the table: the lock is load-bearing; the conditional UPDATE alone does not protect deposit limits or idempotency. |
| 3:50–4:15 | **5. With more time** | Authorize/capture with holds and refunds; Postgres with per-account `SELECT ... FOR UPDATE`; auth; IIAS item-level eligibility. |
| 4:15–4:45 | **6. AI usage** | Be straight about it: Claude wrote the code. What you did: ran it on your Mac and reported the crash, rejected the generic first UI, and asked for a critical review. What caught the AI's mistakes was verification, not trust: the mutation check disproved a claim in its own code comments, and probing the API found bugs that 42 passing tests had missed (an amount that crashed the server, retries returning the wrong result). Only claim checks you actually ran yourself. |

# Demo video outline (target: 4:30, hard limit 5:00)

Record with the app at `http://localhost:8000` on a fresh database (`rm -rf data/` first),
`architecture.md` open in a second tab, and a terminal ready.

| Time | Section | What to show / say |
| --- | --- | --- |
| 0:00–0:20 | Intro | One sentence on what an HSA is; one sentence on the stack (Flask, SQLite, vanilla JS, no build step). |
| 0:20–1:40 | **1. Demo** | Create an account (point out DOB + coverage type drive the IRS limit). Deposit $100. Issue a card (full number shown once, only last 4 stored). Swipe Pharmacy: approved. Swipe Restaurant: declined, "not a qualified medical expense". Freeze card, swipe: declined. Try depositing $5,000: limit error. |
| 1:40–2:10 | **The race** | Click "Run the $100 / $80 + $50 race". Read the result: one approved, one declined, ledger consistent. Click it again a couple of times: sometimes $80 wins, sometimes $50; both are correct. Concurrency lab: `10` x 20 against $100 gives exactly 10 approvals. |
| 2:10–2:40 | **2. Architecture** | Show the layer diagram: UI → thin Flask routes → framework-free service layer → SQLite. The service layer takes a plain connection, which is why tests can drive it from 40 threads. |
| 2:40–3:05 | **3. Data model** | ER diagram. Integer cents. Stored balance plus ledger rows with the invariant tested. Declines recorded. DB-level CHECKs and the partial unique index for one live card. |
| 3:05–3:50 | **4. Transaction processing** | Sequence diagram: BEGIN IMMEDIATE → idempotency → card/account/MCC checks → conditional UPDATE → insert → commit. Explain the check-then-act race and why the naive version loses money even though the balance stays positive. Run `python scripts/mutation_check.py` and point at the table: the lock is load-bearing; the conditional UPDATE alone does not protect deposit limits or idempotency. |
| 3:50–4:15 | **5. With more time** | Authorize/capture with holds and refunds; Postgres with per-account `SELECT ... FOR UPDATE`; auth; IIAS item-level eligibility. |
| 4:15–4:45 | **6. AI usage** | Claude generated the first draft of everything. What I owned: the verification strategy. "All tests passed first time" was not evidence, so I added a naive-implementation control and mutation testing, which caught a wrong claim in the code's own documentation. Mention the other fixes briefly. |

-- HSA platform schema (SQLite).
-- Money is always stored as INTEGER cents. Never floats.

CREATE TABLE IF NOT EXISTS accounts (
    id              TEXT PRIMARY KEY,                 -- e.g. acct_3f9a1c2b4d5e6f70
    holder_name     TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    date_of_birth   TEXT NOT NULL,                    -- ISO date; drives the 55+ catch-up limit
    coverage_type   TEXT NOT NULL CHECK (coverage_type IN ('self_only', 'family')),
    -- Denormalised running balance. The source of truth for "can I spend X"
    -- and the only row that concurrent purchases contend on. The CHECK is the
    -- last line of defence: even buggy application code cannot drive it negative.
    balance_cents   INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deposits (
    id               TEXT PRIMARY KEY,
    account_id       TEXT NOT NULL REFERENCES accounts(id),
    amount_cents     INTEGER NOT NULL CHECK (amount_cents > 0),
    tax_year         INTEGER NOT NULL,                -- which IRS annual limit this counts toward
    idempotency_key  TEXT NOT NULL UNIQUE,
    balance_after_cents INTEGER NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deposits_account_year ON deposits(account_id, tax_year);

CREATE TABLE IF NOT EXISTS cards (
    id              TEXT PRIMARY KEY,                 -- stands in for a network token
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    last4           TEXT NOT NULL,                    -- full PAN is never stored
    exp_month       INTEGER NOT NULL,
    exp_year        INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('active', 'frozen', 'cancelled')),
    created_at      TEXT NOT NULL
);
-- At most one live (active or frozen) card per account, enforced by the database.
CREATE UNIQUE INDEX IF NOT EXISTS uq_one_live_card_per_account
    ON cards(account_id) WHERE status IN ('active', 'frozen');

CREATE TABLE IF NOT EXISTS transactions (
    id                  TEXT PRIMARY KEY,
    idempotency_key     TEXT NOT NULL UNIQUE,         -- safe retries from the card network
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    card_id             TEXT NOT NULL REFERENCES cards(id),
    merchant_name       TEXT NOT NULL,
    mcc                 TEXT NOT NULL,                -- ISO 18245 merchant category code
    amount_cents        INTEGER NOT NULL CHECK (amount_cents > 0),
    status              TEXT NOT NULL CHECK (status IN ('approved', 'declined')),
    decline_reason      TEXT,                         -- NULL when approved
    balance_after_cents INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    CHECK ((status = 'approved') = (decline_reason IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_id, created_at);

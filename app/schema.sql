-- HSA platform schema (SQLite). Money is always INTEGER cents, never a float.
-- Bump db.SCHEMA_VERSION and the user_version line at the bottom together.

CREATE TABLE accounts (
    id              TEXT PRIMARY KEY,                 -- acct_<16 hex>
    holder_name     TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    date_of_birth   TEXT NOT NULL,                    -- ISO date; decides the 55+ catch-up limit
    coverage_type   TEXT NOT NULL CHECK (coverage_type IN ('self_only', 'family')),
    -- Running balance: the one row concurrent purchases contend on. The CHECK is a
    -- last line of defence; application bugs can't drive it below zero.
    balance_cents   INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
    created_at      TEXT NOT NULL
);

CREATE TABLE deposits (
    id                  TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    amount_cents        INTEGER NOT NULL CHECK (amount_cents > 0),
    tax_year            INTEGER NOT NULL,             -- which IRS annual limit it counts toward
    idempotency_key     TEXT NOT NULL,
    balance_after_cents INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    UNIQUE (account_id, idempotency_key)              -- a retry key only means something per account
);
CREATE INDEX idx_deposits_account_year ON deposits(account_id, tax_year);

CREATE TABLE cards (
    id          TEXT PRIMARY KEY,                     -- plays the role of a network token
    account_id  TEXT NOT NULL REFERENCES accounts(id),
    last4       TEXT NOT NULL,                        -- the full card number is never stored
    exp_month   INTEGER NOT NULL,
    exp_year    INTEGER NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('active', 'frozen', 'cancelled')),
    created_at  TEXT NOT NULL
);
-- At most one live (active or frozen) card per account, enforced by the database.
CREATE UNIQUE INDEX uq_one_live_card_per_account
    ON cards(account_id) WHERE status IN ('active', 'frozen');

CREATE TABLE transactions (
    id                  TEXT PRIMARY KEY,
    idempotency_key     TEXT NOT NULL,
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    card_id             TEXT NOT NULL REFERENCES cards(id),
    merchant_name       TEXT NOT NULL,
    mcc                 TEXT NOT NULL,                -- ISO 18245 merchant category code
    amount_cents        INTEGER NOT NULL CHECK (amount_cents > 0),
    status              TEXT NOT NULL CHECK (status IN ('approved', 'declined')),
    decline_reason      TEXT,                         -- NULL exactly when approved
    balance_after_cents INTEGER NOT NULL,             -- balance the decision saw (or left)
    created_at          TEXT NOT NULL,
    CHECK ((status = 'approved') = (decline_reason IS NULL)),
    UNIQUE (card_id, idempotency_key)                 -- a retry from the network is per card
);
CREATE INDEX idx_transactions_account ON transactions(account_id, created_at);

PRAGMA user_version = 2;

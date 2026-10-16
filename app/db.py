"""SQLite connection handling.

Each request gets its own connection (SQLite connections must not be shared
across threads). Writes go through `write_transaction`, which opens the
transaction with BEGIN IMMEDIATE so the write lock is taken *before* any reads.
That turns every check-then-write sequence into a serialised critical section.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# How long a writer waits for the lock before giving up. Purchases hold the
# lock for well under a millisecond, so this is only reached under pathological load.
BUSY_TIMEOUT_MS = 10_000


def connect(db_path: str) -> sqlite3.Connection:
    # isolation_level=None disables Python's implicit transaction handling so we
    # control BEGIN/COMMIT explicitly.
    conn = sqlite3.connect(
        db_path,
        timeout=BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def init_db(db_path: str) -> None:
    conn = connect(db_path)
    try:
        # WAL lets readers (dashboard polling) proceed while a purchase is writing.
        if db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA_PATH.read_text())
    finally:
        conn.close()


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block as one atomic, serialised write transaction.

    BEGIN IMMEDIATE (rather than the default DEFERRED) matters: a deferred
    transaction starts as a reader and tries to upgrade to a writer on its first
    write. Two concurrent deferred transactions that both read then both write
    deadlock, and SQLite fails one with "database is locked" immediately,
    without honouring busy_timeout. IMMEDIATE takes the write lock up front, so
    the second writer simply waits its turn.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")

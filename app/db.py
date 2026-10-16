"""SQLite connections and the one function every write goes through."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 2

# How long a writer waits for the lock before giving up with "database is locked".
# A purchase holds the lock for about a millisecond, so this is only hit under
# pathological load; api.py turns it into a 503 "try again".
LOCK_TIMEOUT_SECONDS = 10


class SchemaMismatch(RuntimeError):
    pass


def connect(db_path: str) -> sqlite3.Connection:
    # One connection per request or thread. isolation_level=None turns off the
    # sqlite3 module's implicit BEGINs so write_transaction controls them.
    conn = sqlite3.connect(db_path, timeout=LOCK_TIMEOUT_SECONDS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    """Create the schema in a new database, or refuse to open an incompatible one."""
    conn = connect(db_path)
    try:
        # WAL lets the UI keep reading while a purchase is writing.
        conn.execute("PRAGMA journal_mode = WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        has_tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
        if has_tables:
            raise SchemaMismatch(
                f"{db_path} was created by an older version of this app (schema {version}, "
                f"need {SCHEMA_VERSION}). Delete that file to start with a fresh database.")
        conn.executescript(SCHEMA_PATH.read_text())
    finally:
        conn.close()


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Run the block as one atomic write transaction, one writer at a time.

    BEGIN IMMEDIATE takes SQLite's write lock *before* the block's first read, so
    "read the balance, decide, write" can't interleave with another writer.
    Plain BEGIN (DEFERRED) would start as a reader and try to upgrade at the
    first write; two transactions that have both read can't both upgrade, and
    SQLite fails one immediately with "database is locked" without waiting.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if conn.in_transaction:     # SQLite may already have rolled back on some errors
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")

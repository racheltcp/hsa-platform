"""Verify that the concurrency tests actually detect concurrency bugs.

Copies the project to a temp directory, deliberately breaks the concurrency
controls one at a time, and runs tests/test_concurrency.py against each broken
copy. A trustworthy test suite must FAIL for every unsafe mutation.

    python scripts/mutation_check.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONDITIONAL_DEBIT = '''            debited = conn.execute(
                "UPDATE accounts SET balance_cents = balance_cents - :amount "
                "WHERE id = :account AND balance_cents >= :amount RETURNING balance_cents",
                {"amount": request["amount_cents"], "account": card["account_id"]}).fetchall()'''
READ_CHECK_WRITE = '''            debited = [] if balance < request["amount_cents"] else conn.execute(
                "UPDATE accounts SET balance_cents = ? WHERE id = ? RETURNING balance_cents",
                (balance - request["amount_cents"], card["account_id"])).fetchall()'''

NO_LOCK = [('conn.execute("BEGIN IMMEDIATE")', "pass"),
           ('conn.execute("ROLLBACK")', "pass"),
           ('conn.execute("COMMIT")', "pass")]
DEFERRED = [('conn.execute("BEGIN IMMEDIATE")', 'conn.execute("BEGIN")')]
NAIVE_DEBIT = [(CONDITIONAL_DEBIT, READ_CHECK_WRITE)]

MUTATIONS = [
    # (name, {file: [(old, new), ...]}, expected_to_be_safe)
    ("baseline (no mutation)", {}, True),
    ("lock only: BEGIN IMMEDIATE + read-check-write in Python", {"app/purchases.py": NAIVE_DEBIT}, True),
    ("BEGIN (deferred) instead of BEGIN IMMEDIATE", {"app/db.py": DEFERRED}, False),
    ("atomic UPDATE only: no transaction lock", {"app/db.py": NO_LOCK}, False),
    ("naive: no lock + read-check-write", {"app/db.py": NO_LOCK, "app/purchases.py": NAIVE_DEBIT}, False),
]


def run(name: str, edits: dict, expected_safe: bool) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        for d in ("app", "tests"):
            shutil.copytree(ROOT / d, Path(tmp) / d)
        for rel, replacements in edits.items():
            path = Path(tmp) / rel
            src = path.read_text()
            for old, new in replacements:
                assert old in src, f"mutation target not found in {rel}"
                src = src.replace(old, new)
            path.write_text(src)
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_concurrency"],
            cwd=tmp, capture_output=True, text=True)
    passed = proc.returncode == 0
    summary = (proc.stderr.strip().splitlines() or ["?"])[-1]
    ok = passed == expected_safe
    verdict = "tests pass" if passed else "tests FAIL"
    print(f"[{'ok' if ok else 'UNEXPECTED'}] {name:<58} -> {verdict:<10} ({summary})")
    return ok


if __name__ == "__main__":
    results = [run(*m) for m in MUTATIONS]
    print("\nAll mutations behaved as expected." if all(results)
          else "\nSome mutations were NOT detected. The tests are not trustworthy.")
    sys.exit(0 if all(results) else 1)

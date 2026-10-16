"""Schema creation and the refusal to open a database from an older version."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import SCHEMA_VERSION, SchemaMismatch, init_db


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.path = str(Path(self._tmp) / "x.db")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_new_database_gets_the_current_schema_and_reopens(self):
        init_db(self.path)
        init_db(self.path)       # second start: nothing to do
        conn = sqlite3.connect(self.path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        conn.close()

    def test_database_from_an_older_version_is_refused_with_a_clear_message(self):
        conn = sqlite3.connect(self.path)
        conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY, status TEXT)")   # what v1 looked like
        conn.commit()
        conn.close()
        with self.assertRaises(SchemaMismatch) as ctx:
            init_db(self.path)
        self.assertIn("Delete that file", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

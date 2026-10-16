"""Start the HSA platform locally: python run.py, then open http://localhost:8000"""
import argparse
import sqlite3
import sys

if sys.version_info < (3, 9) or sqlite3.sqlite_version_info < (3, 35):
    sys.exit(f"This app needs Python 3.9+ with SQLite 3.35+. You have Python "
             f"{sys.version.split()[0]} with SQLite {sqlite3.sqlite_version}.")

from app import create_app  # noqa: E402  (after the version check, which must run first)
from app.db import SchemaMismatch  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the HSA platform")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)   # macOS AirPlay already uses 5000
    parser.add_argument("--db", default=None, help="SQLite file (default: data/hsa.db)")
    args = parser.parse_args()

    try:
        app = create_app(args.db)
    except SchemaMismatch as err:
        sys.exit(str(err))
    print(f"HSA platform running at http://{args.host}:{args.port}")
    # threaded=True gives every request its own thread, so simultaneous purchases
    # really do race each other, as they would behind a production server.
    app.run(host=args.host, port=args.port, threaded=True)

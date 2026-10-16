"""Start the HSA platform locally: python3 run.py  ->  http://localhost:8000"""
import argparse
import sqlite3
import sys

if sys.version_info < (3, 10) or sqlite3.sqlite_version_info < (3, 35):
    sys.exit(f"This app needs Python 3.10+ with SQLite 3.35+. You have Python "
             f"{sys.version.split()[0]} with SQLite {sqlite3.sqlite_version}.")

from app import create_app  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the HSA platform")
    parser.add_argument("--host", default="127.0.0.1")
    # 8000, not 5000: macOS AirPlay Receiver already listens on port 5000.
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None, help="SQLite file (default: data/hsa.db)")
    args = parser.parse_args()

    app = create_app(args.db)
    print(f"HSA platform running at http://{args.host}:{args.port}")
    # threaded=True: every request is handled on its own thread, so concurrent
    # purchases really do race against each other, as they would in production.
    app.run(host=args.host, port=args.port, threaded=True, debug=False)

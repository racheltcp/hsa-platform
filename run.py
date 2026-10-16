"""Start the HSA platform locally: python run.py  ->  http://localhost:5000"""
import argparse

from app import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the HSA platform")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--db", default=None, help="SQLite file (default: data/hsa.db)")
    args = parser.parse_args()

    app = create_app(args.db)
    print(f"HSA platform running at http://{args.host}:{args.port}")
    # threaded=True: every request is handled on its own thread, so concurrent
    # purchases really do race against each other, as they would in production.
    app.run(host=args.host, port=args.port, threaded=True, debug=False)

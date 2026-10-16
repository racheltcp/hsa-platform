"""HSA platform: Flask application factory."""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, send_from_directory

from .api import api
from .db import init_db

ROOT = Path(__file__).resolve().parent.parent


def create_app(database: str | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["DATABASE"] = database or os.environ.get(
        "HSA_DB_PATH", str(ROOT / "data" / "hsa.db")
    )
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    init_db(app.config["DATABASE"])
    app.json.sort_keys = False

    app.register_blueprint(api)

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/healthz")
    def health():
        return {"ok": True}

    return app

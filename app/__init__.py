"""HSA platform: Flask application factory."""
from __future__ import annotations

from pathlib import Path

from flask import Flask, send_from_directory

from .api import api
from .db import init_db

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "hsa.db"


def create_app(database: str | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["DATABASE"] = str(database or DEFAULT_DB)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    init_db(app.config["DATABASE"])
    app.json.sort_keys = False
    app.register_blueprint(api)

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    return app

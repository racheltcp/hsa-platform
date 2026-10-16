"""HTTP routes. Each one parses JSON, calls one function, and returns JSON."""
from __future__ import annotations

import sqlite3

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException

from . import accounts, cards, merchants, purchases
from .db import connect
from .errors import ServiceError, ValidationError

api = Blueprint("api", __name__, url_prefix="/api")


def conn() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


@api.teardown_app_request
def close_conn(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def body() -> dict:
    if not request.get_data():
        return {}
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ServiceError("The request body must be a JSON object.", code="invalid_json")
    return data


def error(status: int, code: str, message: str, field: str | None = None):
    return jsonify({"error": {"code": code, "message": message, "field": field}}), status


# Every failure comes back as JSON with a message a person can read, so the UI
# never has to show "the server answered 500".
@api.errorhandler(ServiceError)
def on_service_error(err: ServiceError):
    return error(err.http_status, err.code, err.message, err.field)


@api.app_errorhandler(HTTPException)
def on_http_error(err: HTTPException):
    # Registered app-wide because a wrong URL or method fails during routing,
    # before Flask knows the request belongs to this blueprint.
    if not request.path.startswith("/api/"):
        return err
    return error(err.code or 500, "http_error", err.description or err.name)


@api.errorhandler(Exception)
def on_unexpected_error(err: Exception):
    if isinstance(err, HTTPException):
        return on_http_error(err)
    if isinstance(err, sqlite3.OperationalError) and "locked" in str(err):
        return error(503, "busy", "The server is busy with other payments. Try again in a moment.")
    current_app.logger.exception("Unhandled error")
    return error(500, "internal_error", "Something went wrong on the server. Nothing was charged.")


@api.get("/merchant-categories")
def merchant_categories():
    return jsonify([{"mcc": c.mcc, "label": c.label, "qualified": c.qualified}
                    for c in merchants.CATEGORIES.values()])


@api.get("/accounts")
def list_accounts():
    return jsonify(accounts.list_accounts(conn()))


@api.post("/accounts")
def create_account():
    return jsonify(accounts.create_account(conn(), body())), 201


@api.get("/accounts/<account_id>")
def get_account(account_id):
    return jsonify(accounts.get_account(conn(), account_id))


@api.get("/accounts/<account_id>/activity")
def account_activity(account_id):
    return jsonify(accounts.list_activity(conn(), account_id))


@api.post("/accounts/<account_id>/deposits")
def create_deposit(account_id):
    deposit, replayed = accounts.deposit(conn(), account_id, body())
    return jsonify(deposit), 200 if replayed else 201


@api.post("/accounts/<account_id>/cards")
def issue_card(account_id):
    data = body()
    replace = data.get("replace", False)

    if not isinstance(replace, bool):
        raise ValidationError("Replace must be true or false.", field="replace")

    return jsonify(cards.issue_card(conn(), account_id, replace=replace)), 201


@api.post("/cards/<card_id>/freeze")
def freeze_card(card_id):
    return jsonify(cards.set_frozen(conn(), card_id, frozen=True))


@api.post("/cards/<card_id>/unfreeze")
def unfreeze_card(card_id):
    return jsonify(cards.set_frozen(conn(), card_id, frozen=False))


@api.post("/transactions")
def create_transaction():
    """A declined purchase is a 201 with status "declined": the request was valid
    and fully processed, the answer was just no."""
    txn, replayed = purchases.authorize_purchase(conn(), body())
    return jsonify(txn), 200 if replayed else 201

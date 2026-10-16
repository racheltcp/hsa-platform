"""HTTP layer: thin Flask routes that parse JSON, call services, and shape responses."""
from __future__ import annotations

import sqlite3

from flask import Blueprint, current_app, g, jsonify, request

from . import merchants, services
from .db import connect
from .errors import ServiceError, ValidationError

api = Blueprint("api", __name__, url_prefix="/api")


def get_conn() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


@api.teardown_app_request
def close_conn(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@api.errorhandler(ServiceError)
def handle_service_error(err: ServiceError):
    return jsonify({"error": {"code": err.code, "message": err.message}}), err.http_status


def body() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    return data


# --- reference data -----------------------------------------------------------------

@api.get("/merchant-categories")
def merchant_categories():
    return jsonify([
        {"mcc": c.mcc, "label": c.label, "group": c.group, "qualified": c.qualified}
        for c in merchants.CATEGORIES.values()
    ])


# --- accounts -----------------------------------------------------------------------

@api.get("/accounts")
def list_accounts():
    return jsonify(services.list_accounts(get_conn()))


@api.post("/accounts")
def create_account():
    return jsonify(services.create_account(get_conn(), body())), 201


@api.get("/accounts/<account_id>")
def get_account(account_id):
    return jsonify(services.get_account(get_conn(), account_id))


@api.get("/accounts/<account_id>/activity")
def account_activity(account_id):
    return jsonify(services.list_activity(get_conn(), account_id))


@api.post("/accounts/<account_id>/deposits")
def create_deposit(account_id):
    deposit, replayed = services.deposit(get_conn(), account_id, body())
    return jsonify(deposit), 200 if replayed else 201


# --- cards --------------------------------------------------------------------------

@api.post("/accounts/<account_id>/cards")
def issue_card(account_id):
    return jsonify(services.issue_card(get_conn(), account_id, body())), 201


@api.post("/cards/<card_id>/freeze")
def freeze_card(card_id):
    return jsonify(services.set_card_status(get_conn(), card_id, "frozen"))


@api.post("/cards/<card_id>/unfreeze")
def unfreeze_card(card_id):
    return jsonify(services.set_card_status(get_conn(), card_id, "active"))


# --- transactions -------------------------------------------------------------------

@api.post("/transactions")
def create_transaction():
    """Authorise a purchase. Declines are 201 with status="declined": a decline is
    a successfully processed request with a negative business outcome."""
    txn, replayed = services.process_transaction(get_conn(), body())
    return jsonify(txn), 200 if replayed else 201

"""Errors the business logic raises; api.py turns them into JSON responses.

A *declined* purchase is not an error. It is a normal outcome and comes back as a
transaction with status "declined". Errors are for requests that can't be
processed at all: bad input, unknown IDs, or a conflict with existing data.
"""
from __future__ import annotations


class ServiceError(Exception):
    http_status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field          # which input caused it, so the UI can point at it
        if code:
            self.code = code


class ValidationError(ServiceError):
    http_status = 422
    code = "validation_error"


class NotFound(ServiceError):
    http_status = 404
    code = "not_found"


class Conflict(ServiceError):
    http_status = 409
    code = "conflict"

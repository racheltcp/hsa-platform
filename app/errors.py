"""Errors raised by the service layer and mapped to HTTP responses by the API.

Note: a *declined* purchase is not an error. It is a normal business outcome
and is returned as a transaction with status "declined". Errors are for
requests that are malformed or reference things that do not exist.
"""


class ServiceError(Exception):
    http_status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
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

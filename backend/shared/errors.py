"""Exception hierarchy for Groundskeeper Lambdas.

Raising one of these in the API Lambda gets mapped to a clean HTTP response by
``responses.error_response``. Any other ``Exception`` becomes a 500.
"""


class GroundskeeperError(Exception):
    """Base for all Lambda-side errors mapped to HTTP responses."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class BadRequest(GroundskeeperError):
    status_code = 400
    code = "bad_request"


class Unauthorized(GroundskeeperError):
    status_code = 401
    code = "unauthorized"


class NotFound(GroundskeeperError):
    status_code = 404
    code = "not_found"


class Conflict(GroundskeeperError):
    status_code = 409
    code = "conflict"


class UpstreamError(GroundskeeperError):
    status_code = 502
    code = "upstream_error"

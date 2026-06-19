"""API Gateway proxy response helpers."""

import json
from typing import Any

from shared.errors import GroundskeeperError

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    "Access-Control-Max-Age": "300",
}


def respond(status: int, body: Any, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    merged = {"Content-Type": "application/json", **_CORS_HEADERS}
    if headers:
        merged.update(headers)
    return {
        "statusCode": status,
        "headers": merged,
        "body": json.dumps(body, default=str),
    }


def ok(body: Any = None, *, status: int = 200) -> dict[str, Any]:
    return respond(status, body if body is not None else {"ok": True})


def no_content() -> dict[str, Any]:
    return {"statusCode": 204, "headers": _CORS_HEADERS, "body": ""}


def error_response(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, GroundskeeperError):
        return respond(exc.status_code, {"error": exc.code, "message": str(exc)})
    return respond(500, {"error": "internal_error", "message": "Unexpected server error."})

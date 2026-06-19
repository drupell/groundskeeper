"""Structured JSON logging for Lambda.

Lambda's runtime captures stdout/stderr into CloudWatch, so we emit one JSON
object per line and let CloudWatch Logs Insights index it. Use ``info``,
``warn``, ``error``, or ``exception`` from any handler.

Per-invocation request IDs are carried in a ``ContextVar`` so concurrent
executions (which share imports but not call stacks) don't bleed IDs into each
other's lines. Each handler should call ``set_request_id(context.aws_request_id)``
at entry.
"""

import contextvars
import json
import sys
import time
import traceback
from typing import Any

_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def set_request_id(request_id: str | None) -> None:
    _REQUEST_ID.set(request_id)


def _emit(level: str, msg: str, **fields: Any) -> None:
    payload: dict[str, Any] = {
        "ts": int(time.time() * 1000),
        "level": level,
        "msg": msg,
    }
    request_id = _REQUEST_ID.get()
    if request_id:
        payload["request_id"] = request_id
    payload.update(fields)
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def info(msg: str, **fields: Any) -> None:
    _emit("INFO", msg, **fields)


def warn(msg: str, **fields: Any) -> None:
    _emit("WARN", msg, **fields)


def error(msg: str, **fields: Any) -> None:
    _emit("ERROR", msg, **fields)


def exception(msg: str, exc: BaseException, **fields: Any) -> None:
    _emit(
        "ERROR",
        msg,
        exc_type=type(exc).__name__,
        exc_message=str(exc),
        traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        **fields,
    )

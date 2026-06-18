"""EventBridge Scheduler helpers — one-time schedules for executor invocations.

Schedule names embed the local target date so that:

  * A failed/interrupted orchestrator run can resume idempotently — same names
    will conflict on re-create rather than double-scheduling.
  * Stale schedules can be reaped by parsing the date out of the name.

Each schedule self-deletes after firing via ``ActionAfterCompletion=DELETE``,
so the only cleanup the orchestrator has to worry about is *unfired*
schedules whose target date is in the past (rare, but possible after errors).
"""

import json
import os
from datetime import datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.errors import UpstreamError

GROUP = "default"
NAME_PREFIX = "groundskeeper-exec-"

_EXECUTOR_ARN = os.environ.get("EXECUTOR_ARN", "")
_ROLE_ARN = os.environ.get("SCHEDULER_ROLE_ARN", "")
_client = boto3.client("scheduler")


def schedule_name(date_str: str, index: int) -> str:
    """``groundskeeper-exec-YYYY-MM-DD-NN`` — stable for a given (date, index)."""
    return f"{NAME_PREFIX}{date_str}-{index:02d}"


def parse_schedule_date(name: str) -> str | None:
    """Inverse of :func:`schedule_name`: ``…-YYYY-MM-DD-NN`` → ``"YYYY-MM-DD"``."""
    if not name.startswith(NAME_PREFIX):
        return None
    tail = name.removeprefix(NAME_PREFIX)
    parts = tail.rsplit("-", 1)
    if len(parts) != 2:
        return None
    date_part = parts[0]
    try:
        datetime.strptime(date_part, "%Y-%m-%d")
    except ValueError:
        return None
    return date_part


def create_executor_schedule(
    name: str,
    *,
    fire_at_utc: datetime,
    payload: dict[str, Any],
) -> None:
    if not _EXECUTOR_ARN or not _ROLE_ARN:
        raise RuntimeError("EXECUTOR_ARN / SCHEDULER_ROLE_ARN env vars aren't set.")
    at_expr = fire_at_utc.strftime("at(%Y-%m-%dT%H:%M:%S)")
    try:
        _client.create_schedule(
            Name=name,
            GroupName=GROUP,
            ScheduleExpression=at_expr,
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
            Target={
                "Arn": _EXECUTOR_ARN,
                "RoleArn": _ROLE_ARN,
                "Input": json.dumps({**payload, "rule_name": name}),
                # Executor handles its own retry budget for transient github errors;
                # scheduler-level retries would compound and possibly fire after
                # ActionAfterCompletion=DELETE has run.
                "RetryPolicy": {
                    "MaximumRetryAttempts": 0,
                    "MaximumEventAgeInSeconds": 600,
                },
            },
        )
    except ClientError as e:
        code = e.response["Error"].get("Code")
        if code == "ConflictException":
            # Already exists — orchestrator probably re-ran for the same date.
            return
        raise UpstreamError(
            f"Couldn't create the scheduler entry: {e.response['Error'].get('Message', e)}",
            code="scheduler_error",
        ) from e


def delete_schedule(name: str) -> None:
    try:
        _client.delete_schedule(Name=name, GroupName=GROUP)
    except ClientError as e:
        if e.response["Error"].get("Code") != "ResourceNotFoundException":
            raise


def list_executor_schedules() -> list[str]:
    names: list[str] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "GroupName": GROUP,
            "NamePrefix": NAME_PREFIX,
            "MaxResults": 100,
        }
        if next_token:
            kwargs["NextToken"] = next_token
        resp = _client.list_schedules(**kwargs)
        for s in resp.get("Schedules", []):
            names.append(s["Name"])
        next_token = resp.get("NextToken")
        if not next_token:
            break
    return names

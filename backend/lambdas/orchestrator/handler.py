"""Groundskeeper — Nightly Orchestrator Lambda.

Daily flow (triggered by EventBridge ``cron(5 0 * * ? *)`` in UTC):

  1. Load the user config from DynamoDB.
  2. Honor vacation mode (skip cleanly if active).
  3. Confirm a repo is configured and a PAT is stored.
  4. Clean up any orphaned one-time schedules from prior failed runs.
  5. Resolve the target local date — today if its window is still ahead,
     otherwise tomorrow.
  6. Sample today's commit count from the user-shaped curve.
  7. Plan commit times inside the day's window respecting gap constraints,
     trimming past times so nothing is scheduled "in the past".
  8. Create one EventBridge Scheduler one-time schedule per commit time,
     with ``ActionAfterCompletion=DELETE`` so they self-clean.
  9. Persist a ``RUN#<local_date>`` record so the dashboard can show
     today's plan.
"""

import json
import os
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import boto3
from botocore.exceptions import ClientError
from shared import config, ddb, log, secrets, timeplan
from shared.distribution import sample_count

USER_ID = "default"
SCHEDULE_GROUP = "default"
SCHEDULE_PREFIX = "groundskeeper-commit"

_DAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

EXECUTOR_ARN = os.environ.get("EXECUTOR_ARN", "")
SCHEDULER_ROLE_ARN = os.environ.get("SCHEDULER_ROLE_ARN", "")

_scheduler = boto3.client("scheduler")


def handler(event: dict, context: Any) -> dict:
    log.set_request_id(getattr(context, "aws_request_id", None))
    log.info("orchestrator.start", event=event)

    # dry_run: compute and return the plan with zero side effects — no orphan
    # cleanup, no schedules created, no RUN# record written. Used by the
    # dashboard's "Preview tonight's plan" action and for local testing.
    dry_run = bool(event.get("dry_run"))

    cfg = config.load_config(USER_ID)
    tz = _safe_zone(cfg.get("timezone", "UTC"))
    now_local = datetime.now(tz)

    skip = _vacation_skip_reason(cfg, now_local.date())
    if skip:
        log.info("orchestrator.skip", reason=skip)
        return {"status": "skipped", "reason": skip, "dry_run": dry_run}

    if not cfg.get("repo"):
        log.warn("orchestrator.skip", reason="no_repo")
        return {"status": "skipped", "reason": "no_repo", "dry_run": dry_run}

    if not secrets.has_pat():
        log.warn("orchestrator.skip", reason="no_pat")
        return {"status": "skipped", "reason": "no_pat", "dry_run": dry_run}

    if not dry_run:
        deleted = _cleanup_orphans()
        if deleted:
            log.info("orchestrator.cleanup", deleted=deleted)

    target_date, day_cfg = _resolve_target_day(cfg["schedule"], now_local)
    if target_date is None:
        log.info("orchestrator.skip", reason="no_active_day_in_window")
        return {
            "status": "skipped",
            "reason": "no_active_day_in_window",
            "dry_run": dry_run,
        }

    cc = cfg["commit_count"]
    sampled = sample_count(int(cc["min"]), int(cc["max"]), cc["curve"])

    times_local, placed = timeplan.plan_times(
        local_date=datetime.combine(target_date, datetime.min.time(), tzinfo=tz),
        tz=tz,
        start_hhmm=day_cfg["start"],
        end_hhmm=day_cfg["end"],
        n=sampled,
        gap_min_minutes=int(cfg["gap"]["min_minutes"]),
        gap_max_minutes=int(cfg["gap"]["max_minutes"]),
    )

    if dry_run:
        log.info(
            "orchestrator.dry_run",
            target_date=target_date.isoformat(),
            sampled=sampled,
            placed=placed,
        )
        return {
            "status": "dry_run",
            "target_date": target_date.isoformat(),
            "timezone": cfg["timezone"],
            "window": {"start": day_cfg["start"], "end": day_cfg["end"]},
            "count_sampled": sampled,
            "planned_times_local": [t.isoformat() for t in times_local],
            "note": (
                "Nothing was scheduled and no run record was written. The "
                "real nightly run also trims any times already in the past."
            ),
        }

    if sampled == 0:
        _persist_run(target_date, cfg["timezone"], sampled, 0, [], [])
        log.info(
            "orchestrator.zero_commits",
            target_date=target_date.isoformat(),
            sampled=0,
        )
        return {
            "status": "planned",
            "target_date": target_date.isoformat(),
            "count_sampled": 0,
            "count_placed": 0,
        }

    future_buffer = now_local + timedelta(minutes=2)
    times_local = [t for t in times_local if t >= future_buffer]
    placed = len(times_local)

    schedule_names: list[str] = []
    times_utc_iso: list[str] = []
    for idx, t_local in enumerate(times_local):
        t_utc = t_local.astimezone(UTC)
        name = f"{SCHEDULE_PREFIX}-{target_date.isoformat()}-" f"{idx:02d}-{uuid.uuid4().hex[:6]}"
        _create_schedule(
            name=name,
            at_utc=t_utc,
            run_date=target_date,
            commit_index=idx,
        )
        schedule_names.append(name)
        times_utc_iso.append(t_utc.isoformat())

    _persist_run(
        target_date,
        cfg["timezone"],
        sampled,
        placed,
        times_utc_iso,
        schedule_names,
    )

    log.info(
        "orchestrator.planned",
        target_date=target_date.isoformat(),
        sampled=sampled,
        placed=placed,
        timezone=cfg["timezone"],
    )
    return {
        "status": "planned",
        "target_date": target_date.isoformat(),
        "count_sampled": sampled,
        "count_placed": placed,
        "schedules": schedule_names,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log.warn("orchestrator.bad_timezone", name=name)
        return ZoneInfo("UTC")


def _vacation_skip_reason(cfg: dict, today_local: date) -> str | None:
    v = cfg.get("vacation") or {}
    if not v.get("active"):
        return None
    until = v.get("until")
    if not until:
        return "vacation_indefinite"
    try:
        until_date = date.fromisoformat(until)
    except (TypeError, ValueError):
        log.warn("orchestrator.bad_vacation_until", until=until)
        return None
    if today_local < until_date:
        return f"vacation_until_{until}"
    return None


def _resolve_target_day(
    schedule_cfg: dict,
    now_local: datetime,
) -> tuple[date | None, dict | None]:
    """Pick today's local date if its window end is still ahead, else tomorrow."""
    for offset in range(2):
        candidate = (now_local + timedelta(days=offset)).date()
        day_cfg = schedule_cfg.get(_DAYS[candidate.weekday()]) or {}
        if not day_cfg.get("enabled"):
            continue
        end_h, end_m = (int(p) for p in day_cfg["end"].split(":"))
        end_dt = datetime.combine(
            candidate,
            datetime.min.time().replace(hour=end_h, minute=end_m),
            tzinfo=now_local.tzinfo,
        )
        if end_dt <= now_local + timedelta(minutes=2):
            continue
        return candidate, day_cfg
    return None, None


def _cleanup_orphans() -> int:
    """Delete any lingering one-time commit schedules under our prefix.

    With ``ActionAfterCompletion=DELETE`` set on every schedule we create, the
    happy path leaves nothing behind. This sweep handles failed/aborted
    executions and re-runs.
    """
    deleted = 0
    next_token: str | None = None
    while True:
        kwargs: dict = {
            "GroupName": SCHEDULE_GROUP,
            "NamePrefix": SCHEDULE_PREFIX,
            "MaxResults": 100,
        }
        if next_token:
            kwargs["NextToken"] = next_token
        resp = _scheduler.list_schedules(**kwargs)
        for schedule in resp.get("Schedules", []):
            try:
                _scheduler.delete_schedule(
                    Name=schedule["Name"],
                    GroupName=SCHEDULE_GROUP,
                )
                deleted += 1
            except ClientError as exc:
                if exc.response["Error"].get("Code") != "ResourceNotFoundException":
                    log.warn(
                        "orchestrator.cleanup_failed",
                        name=schedule["Name"],
                        error=str(exc),
                    )
        next_token = resp.get("NextToken")
        if not next_token:
            break
    return deleted


def _create_schedule(
    *,
    name: str,
    at_utc: datetime,
    run_date: date,
    commit_index: int,
) -> None:
    if not EXECUTOR_ARN or not SCHEDULER_ROLE_ARN:
        raise RuntimeError("EXECUTOR_ARN and SCHEDULER_ROLE_ARN env vars must be set.")
    expression = f"at({at_utc.strftime('%Y-%m-%dT%H:%M:%S')})"
    payload = {
        "schedule_name": name,
        "run_date": run_date.isoformat(),
        "commit_index": commit_index,
        "scheduled_at": at_utc.isoformat(),
    }
    _scheduler.create_schedule(
        Name=name,
        GroupName=SCHEDULE_GROUP,
        ScheduleExpression=expression,
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "OFF"},
        ActionAfterCompletion="DELETE",
        State="ENABLED",
        Target={
            "Arn": EXECUTOR_ARN,
            "RoleArn": SCHEDULER_ROLE_ARN,
            "Input": json.dumps(payload),
        },
    )


def _persist_run(
    target_date: date,
    tz_name: str,
    sampled: int,
    placed: int,
    times_utc_iso: Iterable[str],
    schedule_names: Iterable[str],
) -> None:
    ddb.put_item(
        ddb.user_pk(USER_ID),
        ddb.sk_run(target_date.isoformat()),
        {
            "local_date": target_date.isoformat(),
            "timezone": tz_name,
            "count_sampled": sampled,
            "count_placed": placed,
            "commit_times_utc": list(times_utc_iso),
            "schedule_names": list(schedule_names),
            "status": "planned" if placed > 0 else "skipped",
            "created_at": datetime.now(UTC).isoformat(),
        },
    )

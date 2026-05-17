"""Config schema, defaults, and load/save helpers.

The user's whole dashboard state lives in one DynamoDB item: pk=USER#default,
sk=CONFIG. ``default_config()`` defines the canonical shape; ``validate_config``
enforces it before any write. PUT /config from the dashboard sends the full
object; PATCH-style endpoints (vacation toggle, etc.) compose ``merge_patch``
and ``save_config``.
"""

from typing import Any

from shared import ddb
from shared.errors import BadRequest

DAYS: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def default_config() -> dict[str, Any]:
    return {
        "version": 1,
        "timezone": "UTC",
        "vacation": {"active": False, "until": None},
        "gap": {"min_minutes": 15, "max_minutes": 120},
        "commit_count": {
            "min": 0,
            "max": 4,
            "curve": [
                [0.0, 1.0],
                [0.5, 1.0],
                [1.0, 1.0],
            ],
        },
        "schedule": {
            day: {
                "enabled": day not in ("saturday", "sunday"),
                "start": "09:00",
                "end": "18:00",
            }
            for day in DAYS
        },
        "commit_style": {
            "prompt": (
                "Write code comments and docstrings in the style of a thoughtful, "
                "occasionally tired but passionate developer."
            ),
            "destructive_probability": 0.1,
            "min_lines": 1,
            "max_lines": 8,
        },
        "repo": None,
    }


def load_config(user_id: str = "default") -> dict[str, Any]:
    item = ddb.get_item(ddb.user_pk(user_id), ddb.SK_CONFIG)
    if not item:
        return default_config()
    item.pop("pk", None)
    item.pop("sk", None)
    return item


def save_config(config: dict[str, Any], user_id: str = "default") -> dict[str, Any]:
    validate_config(config)
    ddb.put_item(ddb.user_pk(user_id), ddb.SK_CONFIG, config)
    return config


def merge_patch(patch: dict[str, Any], user_id: str = "default") -> dict[str, Any]:
    """Deep-merge ``patch`` into the current config and persist."""
    current = load_config(user_id)
    merged = _deep_merge(current, patch)
    return save_config(merged, user_id)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_config(c: dict[str, Any]) -> None:
    if not isinstance(c, dict):
        raise BadRequest("Config must be an object.")
    _require(c, "timezone", str)
    _require(c, "vacation", dict)
    _require(c["vacation"], "active", bool)
    if c["vacation"].get("until") is not None and not isinstance(c["vacation"]["until"], str):
        raise BadRequest("vacation.until must be a date string or null.")

    _require(c, "gap", dict)
    _require_int_range(c["gap"], "min_minutes", lo=0, hi=24 * 60)
    _require_int_range(c["gap"], "max_minutes", lo=0, hi=24 * 60)
    if c["gap"]["min_minutes"] > c["gap"]["max_minutes"]:
        raise BadRequest("gap.min_minutes must be ≤ gap.max_minutes.")

    _require(c, "commit_count", dict)
    _require_int_range(c["commit_count"], "min", lo=0, hi=20)
    _require_int_range(c["commit_count"], "max", lo=0, hi=20)
    if c["commit_count"]["min"] > c["commit_count"]["max"]:
        raise BadRequest("commit_count.min must be ≤ commit_count.max.")
    _validate_curve(c["commit_count"].get("curve"))

    _require(c, "schedule", dict)
    for day in DAYS:
        if day not in c["schedule"]:
            raise BadRequest(f"schedule.{day} is missing.")
        day_cfg = c["schedule"][day]
        _require(day_cfg, "enabled", bool)
        _require_time(day_cfg, "start")
        _require_time(day_cfg, "end")

    _require(c, "commit_style", dict)
    _require(c["commit_style"], "prompt", str)
    if not c["commit_style"]["prompt"].strip():
        raise BadRequest("commit_style.prompt must not be empty.")
    p = c["commit_style"].get("destructive_probability", 0)
    if not isinstance(p, (int, float)) or not (0.0 <= float(p) <= 1.0):
        raise BadRequest("commit_style.destructive_probability must be in [0, 1].")
    _require_int_range(c["commit_style"], "min_lines", lo=1, hi=1000)
    _require_int_range(c["commit_style"], "max_lines", lo=1, hi=1000)
    if c["commit_style"]["min_lines"] > c["commit_style"]["max_lines"]:
        raise BadRequest("commit_style.min_lines must be ≤ commit_style.max_lines.")

    if c.get("repo") is not None and not isinstance(c["repo"], dict):
        raise BadRequest("repo must be an object or null.")


def _validate_curve(curve: Any) -> None:
    if not isinstance(curve, list) or len(curve) < 2:
        raise BadRequest("commit_count.curve must be a list of at least 2 points.")
    for pt in curve:
        if not (isinstance(pt, list) and len(pt) == 2):
            raise BadRequest("Each curve point must be a [x, y] pair.")
        x, y = pt
        if not isinstance(x, (int, float)) or not (0.0 <= float(x) <= 1.0):
            raise BadRequest("Curve x values must be numbers in [0, 1].")
        if not isinstance(y, (int, float)) or float(y) < 0.0:
            raise BadRequest("Curve y values must be non-negative numbers.")


def _require(obj: dict[str, Any], key: str, t: type) -> None:
    if key not in obj or not isinstance(obj[key], t):
        raise BadRequest(f"Field '{key}' missing or wrong type (expected {t.__name__}).")


def _require_int_range(obj: dict[str, Any], key: str, *, lo: int, hi: int) -> None:
    v = obj.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
        raise BadRequest(f"Field '{key}' must be an int in [{lo}, {hi}].")


def _require_time(obj: dict[str, Any], key: str) -> None:
    v = obj.get(key)
    if not isinstance(v, str):
        raise BadRequest(f"Field '{key}' must be an 'HH:MM' time string.")
    parts = v.split(":")
    if len(parts) != 2:
        raise BadRequest(f"Field '{key}' must be 'HH:MM'.")
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise BadRequest(f"Field '{key}' must be 'HH:MM'.") from exc
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise BadRequest(f"Field '{key}' has invalid hour/minute.")

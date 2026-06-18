"""DynamoDB helpers around the single-table ``groundskeeper-config`` schema.

Key convention (see also ``project-architecture`` memo):
    pk = "USER#<user_id>"
    sk = one of:
        "CONFIG"              — schedule, distribution, commit-style, vacation.
        "GITHUB"              — verified GitHub user metadata.
        "REPO#<repo_id>"      — repository configuration.
        "RUN#<YYYY-MM-DD>"    — orchestrator run record.
        "LOG#<iso-ts>#<rand>" — per-commit executor log entry.

Boto3 returns ``Decimal`` for numbers, which doesn't serialize cleanly to
JSON. ``to_ddb`` / ``from_ddb`` round-trip Python's native float/int through
``Decimal`` for storage and back for HTTP responses.
"""

import os
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

_TABLE_NAME = os.environ.get("CONFIG_TABLE", "")
_resource = boto3.resource("dynamodb")
_table = _resource.Table(_TABLE_NAME) if _TABLE_NAME else None


def table() -> Any:
    if _table is None:
        raise RuntimeError("CONFIG_TABLE env var is not set.")
    return _table


# ---------------------------------------------------------------------------
# Key builders
# ---------------------------------------------------------------------------
SK_CONFIG = "CONFIG"
SK_GITHUB = "GITHUB"


def user_pk(user_id: str = "default") -> str:
    return f"USER#{user_id}"


def sk_repo(repo_id: str = "default") -> str:
    return f"REPO#{repo_id}"


def sk_run(local_date: str) -> str:
    return f"RUN#{local_date}"


def sk_log(iso_ts: str, suffix: str) -> str:
    return f"LOG#{iso_ts}#{suffix}"


# ---------------------------------------------------------------------------
# Type conversion
# ---------------------------------------------------------------------------
def to_ddb(obj: Any) -> Any:
    """Recursively convert floats to ``Decimal`` for DynamoDB writes."""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        # str() avoids float→Decimal precision artifacts (0.1 → 0.10000000…01).
        return Decimal(str(obj))
    if isinstance(obj, list):
        return [to_ddb(x) for x in obj]
    if isinstance(obj, tuple):
        return [to_ddb(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_ddb(v) for k, v in obj.items()}
    return obj


def from_ddb(obj: Any) -> Any:
    """Recursively convert ``Decimal`` back to Python int/float for JSON output."""
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    if isinstance(obj, list):
        return [from_ddb(x) for x in obj]
    if isinstance(obj, dict):
        return {k: from_ddb(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def get_item(pk: str, sk: str) -> dict[str, Any] | None:
    resp = table().get_item(Key={"pk": pk, "sk": sk})
    item = resp.get("Item")
    return from_ddb(item) if item else None


def put_item(pk: str, sk: str, item: dict[str, Any]) -> None:
    record = {**item, "pk": pk, "sk": sk}
    table().put_item(Item=to_ddb(record))


def put_item_conditional(
    pk: str,
    sk: str,
    item: dict[str, Any],
    expected_version: int | None,
) -> None:
    """Put ``item`` only if the stored item's ``version`` matches ``expected_version``.

    ``expected_version=None`` means "the item must not exist yet" — used for the
    first write. Raises ``botocore.exceptions.ClientError`` with code
    ``ConditionalCheckFailedException`` when a concurrent writer beat us.
    """
    record = {**item, "pk": pk, "sk": sk}
    if expected_version is None:
        table().put_item(
            Item=to_ddb(record),
            ConditionExpression="attribute_not_exists(pk)",
        )
        return
    table().put_item(
        Item=to_ddb(record),
        ConditionExpression="version = :ev",
        ExpressionAttributeValues={":ev": to_ddb(expected_version)},
    )


def update_attrs(pk: str, sk: str, attrs: dict[str, Any]) -> dict[str, Any]:
    if not attrs:
        return get_item(pk, sk) or {}
    set_parts: list[str] = []
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    for i, (k, v) in enumerate(attrs.items()):
        names[f"#k{i}"] = k
        values[f":v{i}"] = to_ddb(v)
        set_parts.append(f"#k{i} = :v{i}")
    resp = table().update_item(
        Key={"pk": pk, "sk": sk},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return from_ddb(resp.get("Attributes", {}))


def delete_item(pk: str, sk: str) -> None:
    table().delete_item(Key={"pk": pk, "sk": sk})


def query_prefix(
    pk: str,
    sk_prefix: str,
    *,
    limit: int = 100,
    ascending: bool = True,
) -> list[dict[str, Any]]:
    resp = table().query(
        KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with(sk_prefix),
        ScanIndexForward=ascending,
        Limit=limit,
    )
    return [from_ddb(item) for item in resp.get("Items", [])]

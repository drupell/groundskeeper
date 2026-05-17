"""GitHub PAT storage via AWS Secrets Manager.

The secret payload is a small JSON object:
    {"value": "<token>", "set": true}

When a user clears their PAT we replace ``value`` with the empty string and
flip ``set`` to false rather than deleting the secret — keeping the same ARN
across PAT rotations means the Lambda's IAM policy doesn't need to be touched.
"""

import json
import os

import boto3
from botocore.exceptions import ClientError

_PAT_SECRET_NAME = os.environ.get("PAT_SECRET_NAME", "")
_client = boto3.client("secretsmanager")


def _require_name() -> str:
    if not _PAT_SECRET_NAME:
        raise RuntimeError("PAT_SECRET_NAME env var is not set.")
    return _PAT_SECRET_NAME


def get_pat() -> str | None:
    name = _require_name()
    try:
        resp = _client.get_secret_value(SecretId=name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise
    try:
        body = json.loads(resp["SecretString"])
    except (json.JSONDecodeError, KeyError):
        return None
    if not body.get("set"):
        return None
    value = body.get("value")
    return value if isinstance(value, str) and value else None


def set_pat(token: str) -> None:
    name = _require_name()
    _client.put_secret_value(
        SecretId=name,
        SecretString=json.dumps({"value": token, "set": True}),
    )


def clear_pat() -> None:
    name = _require_name()
    _client.put_secret_value(
        SecretId=name,
        SecretString=json.dumps({"value": "", "set": False}),
    )


def has_pat() -> bool:
    return get_pat() is not None

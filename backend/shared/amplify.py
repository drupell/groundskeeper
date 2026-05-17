"""Amplify Hosting basic-auth password rotation."""

import base64
import os

import boto3
from botocore.exceptions import ClientError

from shared.errors import BadRequest, NotFound, UpstreamError

_AMPLIFY_APP_NAME = os.environ.get("AMPLIFY_APP_NAME", "")
_USERNAME = os.environ.get("AMPLIFY_BASIC_AUTH_USERNAME", "admin")
_client = boto3.client("amplify")
_cached_app_id: str | None = None


def _find_app_id() -> str:
    global _cached_app_id
    if _cached_app_id:
        return _cached_app_id
    if not _AMPLIFY_APP_NAME:
        raise UpstreamError("AMPLIFY_APP_NAME env var is not set.", code="config_missing")
    next_token: str | None = None
    while True:
        kwargs: dict = {"maxResults": 100}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = _client.list_apps(**kwargs)
        for a in resp.get("apps", []):
            if a["name"] == _AMPLIFY_APP_NAME:
                _cached_app_id = a["appId"]
                return _cached_app_id
        next_token = resp.get("nextToken")
        if not next_token:
            break
    raise NotFound(f"Amplify app '{_AMPLIFY_APP_NAME}' not found.")


def rotate_password(new_password: str) -> None:
    if not isinstance(new_password, str) or len(new_password) < 8:
        raise BadRequest("Password must be a string of at least 8 characters.")
    app_id = _find_app_id()
    creds = base64.b64encode(f"{_USERNAME}:{new_password}".encode()).decode()
    try:
        _client.update_app(
            appId=app_id,
            enableBasicAuth=True,
            basicAuthCredentials=creds,
        )
    except ClientError as e:
        raise UpstreamError(
            f"Amplify rejected the update: {e.response['Error'].get('Message', e)}",
            code="amplify_error",
        ) from e

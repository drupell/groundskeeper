"""Groundskeeper dashboard API Lambda.

Single Lambda fronted by an API Gateway ``{proxy+}`` resource. Dispatches on
``(httpMethod, path)`` to the handlers below. Every handler either returns an
API Gateway proxy response dict (via ``shared.responses.ok``) or raises a
``GroundskeeperError`` subclass that the top-level wrapper converts to an
appropriate HTTP error.

Endpoints
---------
    GET    /health             health check
    GET    /config             load full user config (defaults if unset)
    PUT    /config             replace full user config (validates)
    PATCH  /config             deep-merge a partial config patch
    POST   /github/verify      verify a PAT, return user info + scopes
    PUT    /github/pat         verify, then store the PAT in Secrets Manager
    DELETE /github/pat         clear the stored PAT
    GET    /github/status      stored GitHub user metadata + connected flag
    GET    /github/repo        live repo metadata using the stored PAT
    PUT    /github/repo        validate a repo URL with the PAT, persist it
    GET    /logs?limit=N       recent executor commit log entries
    GET    /runs?limit=N       recent orchestrator run records
    GET    /status             composite status (vacation, last run, recent logs)
    PUT    /vacation           toggle vacation mode
    POST   /password           rotate the Amplify basic-auth password
"""

import base64
import json
import os
from collections.abc import Callable
from typing import Any

import boto3
from shared import config as cfg
from shared import ddb, log, secrets
from shared.amplify import rotate_password
from shared.errors import BadRequest, GroundskeeperError, NotFound, UpstreamError
from shared.github import get_last_commit, get_repo, parse_repo_url, verify_token
from shared.responses import error_response, no_content, ok

Handler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]

_lambda = boto3.client("lambda")
_EXECUTOR_NAME = os.environ.get("EXECUTOR_NAME", "")
_ORCHESTRATOR_NAME = os.environ.get("ORCHESTRATOR_NAME", "")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    log.set_request_id(getattr(context, "aws_request_id", None))
    method = (event.get("httpMethod") or "GET").upper()
    path = _normalize_path(event.get("path") or "/")
    log.info("api.request", method=method, path=path)

    if method == "OPTIONS":
        return no_content()

    try:
        body = _parse_body(event)
        route = _resolve(method, path)
        result = route(event, body)
        log.info("api.response", method=method, path=path, status=result.get("statusCode"))
        return result
    except GroundskeeperError as e:
        log.info("api.client_error", method=method, path=path, code=e.code, message=str(e))
        return error_response(e)
    except Exception as e:
        log.exception("api.handler_error", e, method=method, path=path)
        return error_response(e)


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------
def _normalize_path(path: str) -> str:
    """Strip trailing slash so /config and /config/ both route the same."""
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode()
        except (ValueError, UnicodeDecodeError) as e:
            raise BadRequest("Body is not valid base64 / UTF-8.") from e
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BadRequest("Request body must be valid JSON.") from e
    if not isinstance(parsed, dict):
        raise BadRequest("Request body must be a JSON object.")
    return parsed


def _query_int(event: dict[str, Any], key: str, *, default: int, lo: int, hi: int) -> int:
    params = event.get("queryStringParameters") or {}
    raw = params.get(key)
    if raw is None:
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError) as e:
        raise BadRequest(f"Query param '{key}' must be an integer.") from e
    if not (lo <= v <= hi):
        raise BadRequest(f"Query param '{key}' must be in [{lo}, {hi}].")
    return v


def _require_pat() -> str:
    token = secrets.get_pat()
    if not token:
        raise BadRequest("No GitHub PAT is stored. Set one first via PUT /github/pat.")
    return token


def _strip_keys(item: dict[str, Any]) -> dict[str, Any]:
    item.pop("pk", None)
    item.pop("sk", None)
    return item


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def _resolve(method: str, path: str) -> Handler:
    route = _ROUTES.get((method, path))
    if route is None:
        raise NotFound(f"No route for {method} {path}.")
    return route


def _health(_event: dict[str, Any], _body: dict[str, Any]) -> dict[str, Any]:
    return ok({"status": "ok"})


# Config ----------------------------------------------------------------------
def _get_config(_event: dict[str, Any], _body: dict[str, Any]) -> dict[str, Any]:
    return ok(cfg.load_config())


def _put_config(_event: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return ok(cfg.save_config(body))


def _patch_config(_event: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return ok(cfg.merge_patch(body))


# GitHub ----------------------------------------------------------------------
def _github_verify(_event: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    user = verify_token(body.get("token", ""))
    return ok(user)


def _github_set_pat(_event: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    token = body.get("token", "")
    user = verify_token(token)
    if not user["sufficient"]:
        raise BadRequest("PAT is missing the required 'repo' scope.")
    secrets.set_pat(token)
    record = {k: v for k, v in user.items() if not k.startswith("_")}
    ddb.put_item(ddb.user_pk(), ddb.SK_GITHUB, record)
    return ok({**record, "connected": True})


def _github_clear_pat(_event: dict[str, Any], _body: dict[str, Any]) -> dict[str, Any]:
    secrets.clear_pat()
    ddb.delete_item(ddb.user_pk(), ddb.SK_GITHUB)
    return ok({"connected": False})


def _github_status(_event: dict[str, Any], _body: dict[str, Any]) -> dict[str, Any]:
    record = ddb.get_item(ddb.user_pk(), ddb.SK_GITHUB) or {}
    _strip_keys(record)
    return ok({**record, "connected": secrets.has_pat()})


def _github_get_repo(_event: dict[str, Any], _body: dict[str, Any]) -> dict[str, Any]:
    token = _require_pat()
    config = cfg.load_config()
    repo_info = config.get("repo")
    if not repo_info or not repo_info.get("owner") or not repo_info.get("name"):
        raise BadRequest("No repo is configured.")
    fresh = get_repo(token, repo_info["owner"], repo_info["name"])
    last = get_last_commit(token, repo_info["owner"], repo_info["name"], fresh["default_branch"])
    return ok({**fresh, "last_commit": last})


def _github_set_repo(_event: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    token = _require_pat()
    owner, name = parse_repo_url(body.get("url", ""))
    fresh = get_repo(token, owner, name)
    if not fresh["permissions"].get("push"):
        raise BadRequest("PAT does not have push access to this repository.")
    last = get_last_commit(token, owner, name, fresh["default_branch"])
    saved = {
        "url": fresh["html_url"],
        "owner": owner,
        "name": name,
        "full_name": fresh["full_name"],
        "default_branch": fresh["default_branch"],
        "description": fresh.get("description"),
        "private": fresh["private"],
        "repo_id": "default",
    }
    cfg.merge_patch({"repo": saved})
    return ok({**saved, "last_commit": last})


# Logs + runs -----------------------------------------------------------------
def _list_logs(event: dict[str, Any], _body: dict[str, Any]) -> dict[str, Any]:
    limit = _query_int(event, "limit", default=50, lo=1, hi=500)
    items = ddb.query_prefix(ddb.user_pk(), "LOG#", limit=limit, ascending=False)
    return ok({"items": [_strip_keys(i) for i in items]})


def _list_runs(event: dict[str, Any], _body: dict[str, Any]) -> dict[str, Any]:
    limit = _query_int(event, "limit", default=14, lo=1, hi=180)
    items = ddb.query_prefix(ddb.user_pk(), "RUN#", limit=limit, ascending=False)
    return ok({"items": [_strip_keys(i) for i in items]})


def _dashboard_status(_event: dict[str, Any], _body: dict[str, Any]) -> dict[str, Any]:
    config = cfg.load_config()
    runs = ddb.query_prefix(ddb.user_pk(), "RUN#", limit=1, ascending=False)
    last_run = _strip_keys(runs[0]) if runs else None
    recent_logs = [
        _strip_keys(i) for i in ddb.query_prefix(ddb.user_pk(), "LOG#", limit=5, ascending=False)
    ]
    return ok(
        {
            "vacation": config["vacation"],
            "timezone": config["timezone"],
            "last_run": last_run,
            "recent_logs": recent_logs,
        }
    )


# Vacation + password ---------------------------------------------------------
def _put_vacation(_event: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    if "active" not in body or not isinstance(body["active"], bool):
        raise BadRequest("Field 'active' (boolean) is required.")
    until = body.get("until")
    if until is not None and not isinstance(until, str):
        raise BadRequest("Field 'until' must be a date string or null.")
    return ok(cfg.merge_patch({"vacation": {"active": body["active"], "until": until}}))


def _rotate_password(_event: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    rotate_password(body.get("new", ""))
    return ok({"rotated": True})


# Test helpers ----------------------------------------------------------------
def _test_run(_event: dict[str, Any], _body: dict[str, Any]) -> dict[str, Any]:
    """Fire the executor once, right now — async so we never hit the 29s API
    Gateway timeout. The result lands on the Logs page within seconds."""
    if not _EXECUTOR_NAME:
        raise UpstreamError("Executor function name is not configured.", code="config_missing")
    _lambda.invoke(FunctionName=_EXECUTOR_NAME, InvocationType="Event", Payload=b"{}")
    log.info("api.test_run.triggered", executor=_EXECUTOR_NAME)
    return ok(
        {
            "status": "triggered",
            "message": "Executor invoked. Watch the Logs page — the result lands in a few seconds.",
        }
    )


def _test_plan(_event: dict[str, Any], _body: dict[str, Any]) -> dict[str, Any]:
    """Synchronously run the orchestrator in dry-run mode and return its plan.
    Dry-run does no GitHub/Bedrock work, so it returns well within the timeout."""
    if not _ORCHESTRATOR_NAME:
        raise UpstreamError("Orchestrator function name is not configured.", code="config_missing")
    resp = _lambda.invoke(
        FunctionName=_ORCHESTRATOR_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps({"dry_run": True}).encode(),
    )
    payload = json.loads(resp["Payload"].read() or b"{}")
    if resp.get("FunctionError"):
        raise UpstreamError(
            f"Orchestrator dry-run failed: {payload}",
            code="dry_run_failed",
        )
    return ok(payload)


_ROUTES: dict[tuple[str, str], Handler] = {
    ("GET", "/health"): _health,
    ("GET", "/config"): _get_config,
    ("PUT", "/config"): _put_config,
    ("PATCH", "/config"): _patch_config,
    ("POST", "/github/verify"): _github_verify,
    ("PUT", "/github/pat"): _github_set_pat,
    ("DELETE", "/github/pat"): _github_clear_pat,
    ("GET", "/github/status"): _github_status,
    ("GET", "/github/repo"): _github_get_repo,
    ("PUT", "/github/repo"): _github_set_repo,
    ("GET", "/logs"): _list_logs,
    ("GET", "/runs"): _list_runs,
    ("GET", "/status"): _dashboard_status,
    ("PUT", "/vacation"): _put_vacation,
    ("POST", "/password"): _rotate_password,
    ("POST", "/test/run"): _test_run,
    ("POST", "/test/plan"): _test_plan,
}

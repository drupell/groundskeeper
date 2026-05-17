"""Groundskeeper — Commit Executor Lambda.

Triggered by a one-time EventBridge Scheduler rule that the orchestrator
created. Picks a random eligible file from the configured repo, asks Bedrock
Nova Lite for a creative or destructive edit, writes the result back via the
GitHub Contents API as a commit, and records the outcome in DynamoDB. The
scheduler rule self-deletes on completion (``ActionAfterCompletion=DELETE``)
so the executor never has to clean up after itself.
"""

import random
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from shared import bedrock, config, ddb, files, github, log, secrets
from shared.errors import GroundskeeperError, UpstreamError

_AVATAR_ID_RE = re.compile(r"/u/(\d+)")


def _resolve_author(identity: dict) -> tuple[str, str]:
    """Resolve the commit author name + a GitHub-attributable email.

    Contributions only count when the author email is tied to the account:
    a verified email, or the account's ``{id}+{login}@users.noreply.github.com``
    privacy email. We prefer the ``commit_email`` that ``verify_token`` now
    stores; for identities saved before that existed we reconstruct the
    noreply address from the id (falling back to digging it out of the avatar
    URL, then to a login-only noreply). If we can't produce an attributable
    address we refuse to commit rather than silently author junk that won't
    count toward the graph.
    """
    name = identity.get("name") or identity.get("login")
    email = identity.get("commit_email")
    if not email:
        uid = identity.get("id")
        if not uid:
            m = _AVATAR_ID_RE.search(identity.get("avatar_url") or "")
            uid = m.group(1) if m else None
        email = github.noreply_email(uid, identity.get("login"))
    if not name or not email:
        raise GroundskeeperError(
            "No GitHub-attributable author identity stored. Reconnect your PAT "
            "on the GitHub page so commits count toward your graph.",
            code="no_author_identity",
        )
    return name, email


USER_ID = "default"

_MAX_FILE_BYTES = 200 * 1024


_CREATIVE_MESSAGES: tuple[str, ...] = (
    "docs: small tweak in {name}",
    "chore: clarify {name}",
    "docs: tidy {name}",
    "chore: update {name}",
    "docs: add notes to {name}",
)
_DESTRUCTIVE_MESSAGES: tuple[str, ...] = (
    "chore: drop unused lines in {name}",
    "chore: tidy {name}",
    "refactor: trim {name}",
    "chore: clean up {name}",
)


def handler(event: dict, context: Any) -> dict:
    log.set_request_id(getattr(context, "aws_request_id", None))
    log.info("executor.start", event=event)
    try:
        return _run(event)
    except GroundskeeperError as exc:
        log.warn("executor.skipped", code=exc.code, message=str(exc))
        _record_failure(event, code=exc.code, message=str(exc))
        return {"status": "skipped", "reason": exc.code}
    except Exception as exc:
        log.exception("executor.failure", exc)
        _record_failure(event, code="unhandled", message=str(exc))
        raise


# ---------------------------------------------------------------------------
# Core flow
# ---------------------------------------------------------------------------
def _run(event: dict) -> dict:
    cfg = config.load_config(USER_ID)
    repo_cfg = cfg.get("repo")
    if not repo_cfg:
        raise GroundskeeperError("No repo configured.", code="no_repo")

    token = secrets.get_pat()
    if not token:
        raise GroundskeeperError("No PAT stored.", code="no_pat")

    identity = ddb.get_item(ddb.user_pk(USER_ID), ddb.SK_GITHUB) or {}
    author_name, author_email = _resolve_author(identity)

    owner = repo_cfg["owner"]
    repo_name = repo_cfg["name"]
    branch = repo_cfg["default_branch"]

    tree = github.list_tree(token, owner, repo_name, branch)
    candidates = _filter_files(tree)
    if not candidates:
        raise GroundskeeperError("No eligible files in the repo.", code="no_files")
    chosen = random.choice(candidates)
    path: str = chosen["path"]
    log.info("executor.file_chosen", path=path, size=int(chosen.get("size") or 0))

    file_obj = github.get_file(token, owner, repo_name, path, branch)
    current_content = github.decode_file(file_obj)

    commit_type = _choose_commit_type(cfg, current_content)
    new_content = _edit(commit_type, path, current_content, cfg["commit_style"])

    if new_content == current_content:
        raise GroundskeeperError("Bedrock returned no change.", code="no_change")

    message = _build_commit_message(commit_type, path)
    result = github.put_file(
        token,
        owner,
        repo_name,
        path,
        branch,
        content=new_content,
        sha=file_obj.get("sha"),
        message=message,
        author_name=author_name,
        author_email=author_email,
    )
    commit_meta = result.get("commit") or {}

    _record_success(
        event=event,
        commit_type=commit_type,
        path=path,
        message=message,
        commit_meta=commit_meta,
    )
    log.info(
        "executor.committed",
        path=path,
        sha=commit_meta.get("sha"),
        type=commit_type,
    )
    return {
        "status": "committed",
        "type": commit_type,
        "path": path,
        "sha": commit_meta.get("sha"),
        "commit_url": commit_meta.get("html_url"),
    }


# ---------------------------------------------------------------------------
# File selection
# ---------------------------------------------------------------------------
def _filter_files(tree: list[dict]) -> list[dict]:
    out: list[dict] = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        if not path:
            continue
        size = int(node.get("size") or 0)
        if size == 0 or size > _MAX_FILE_BYTES:
            continue
        if not files.is_editable(path):
            continue
        out.append(node)
    return out


# ---------------------------------------------------------------------------
# Edit decision + Bedrock call
# ---------------------------------------------------------------------------
def _choose_commit_type(cfg: dict, content: str) -> str:
    prob = float(cfg["commit_style"].get("destructive_probability", 0.0))
    if random.random() >= prob:
        return "creative"
    # Don't destroy a near-empty file.
    significant_lines = sum(1 for line in content.splitlines() if line.strip())
    if significant_lines < 3:
        return "creative"
    return "destructive"


def _edit(commit_type: str, path: str, content: str, style: dict) -> str:
    ext = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if commit_type == "destructive":
        system, user = bedrock.destructive_edit_prompt(
            file_path=path,
            file_extension=ext,
            file_text=content,
        )
        temperature = 0.4
    else:
        system, user = bedrock.creative_edit_prompt(
            file_path=path,
            file_extension=ext,
            file_text=content,
            style_prompt=style["prompt"],
            min_lines=int(style.get("min_lines", 1)),
            max_lines=int(style.get("max_lines", 8)),
        )
        temperature = 0.7
    reply = bedrock.generate_edit(
        system_prompt=system,
        user_prompt=user,
        max_tokens=4096,
        temperature=temperature,
    )
    if not reply.strip():
        raise UpstreamError("Bedrock returned an empty response.", code="bedrock_empty")
    # generate_edit already strips one optional code fence — guard against the
    # rare case where the model wraps in two layers of fences.
    return reply.rstrip("\n") + "\n"


def _build_commit_message(commit_type: str, path: str) -> str:
    name = path.rsplit("/", 1)[-1] or path
    templates = _DESTRUCTIVE_MESSAGES if commit_type == "destructive" else _CREATIVE_MESSAGES
    return random.choice(templates).format(name=name)


# ---------------------------------------------------------------------------
# Run logging
# ---------------------------------------------------------------------------
def _record_success(
    *,
    event: dict,
    commit_type: str,
    path: str,
    message: str,
    commit_meta: dict,
) -> None:
    now = datetime.now(UTC)
    ddb.put_item(
        ddb.user_pk(USER_ID),
        ddb.sk_log(now.isoformat(), uuid.uuid4().hex[:6]),
        {
            "status": "ok",
            "type": commit_type,
            "path": path,
            "sha": commit_meta.get("sha"),
            "commit_url": commit_meta.get("html_url"),
            "message": message,
            "scheduled_at": event.get("scheduled_at"),
            "run_date": event.get("run_date"),
            "schedule_name": event.get("schedule_name"),
            "committed_at": now.isoformat(),
        },
    )


def _record_failure(event: dict, *, code: str, message: str) -> None:
    now = datetime.now(UTC)
    try:
        ddb.put_item(
            ddb.user_pk(USER_ID),
            ddb.sk_log(now.isoformat(), uuid.uuid4().hex[:6]),
            {
                "status": "failed",
                "code": code,
                "message": message[:500],
                "scheduled_at": event.get("scheduled_at"),
                "run_date": event.get("run_date"),
                "schedule_name": event.get("schedule_name"),
                "logged_at": now.isoformat(),
            },
        )
    except Exception as exc:
        log.warn("executor.log_failure_swallowed", error=str(exc))

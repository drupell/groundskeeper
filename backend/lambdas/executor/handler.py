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
            "We don't have a GitHub-attributable author on file. Reconnect your "
            "token on the GitHub page so commits count toward your graph.",
            code="no_author_identity",
        )
    return name, email


USER_ID = "default"

# Cap candidate files well below Bedrock's 4096-token output budget. At Nova's
# rough ~3:1 char-per-token ratio, 12 KB of source fits with room to spare so
# the model can return the full file without hitting max_tokens and silently
# truncating the commit.
_MAX_FILE_BYTES = 12 * 1024
# How many different files to try if the first picks 404 from the contents API
# (rare quirky paths) or come back unchanged from Bedrock. Each retry simply
# picks a fresh candidate from the same listing.
_MAX_FILE_ATTEMPTS = 5


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
        logged = _record_failure(event, code=exc.code, message=str(exc))
        return {"status": "skipped", "reason": exc.code, "logged": logged}
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
        raise GroundskeeperError(
            "No repository connected yet — add one on the GitHub page.",
            code="no_repo",
        )

    token = secrets.get_pat()
    if not token:
        raise GroundskeeperError(
            "No GitHub token saved yet — connect one on the GitHub page first.",
            code="no_pat",
        )

    # Re-anchor on GitHub's stable numeric repo id every run so a rename or
    # ownership transfer heals through silently instead of failing the PUT
    # later with "Moved Permanently".
    repo_cfg, _changed = github.resolve_current(token, repo_cfg)
    if _changed:
        log.info("executor.repo_reconciled", to_full=repo_cfg.get("full_name"))
        config.merge_patch({"repo": repo_cfg}, USER_ID)

    identity = ddb.get_item(ddb.user_pk(USER_ID), ddb.SK_GITHUB) or {}
    author_name, author_email = _resolve_author(identity)

    owner = repo_cfg["owner"]
    repo_name = repo_cfg["name"]
    branch = repo_cfg["default_branch"]

    tree = github.list_tree(token, owner, repo_name, branch)
    candidates = _filter_files(tree)
    if not candidates:
        raise GroundskeeperError(
            "Couldn't find an eligible file to edit in this repo.",
            code="no_files",
        )

    # Pick a file, fetch it, ask the model to edit it. Some failures are
    # *file-specific* — a 404 from the contents API (unusual paths, races), or
    # Bedrock returning the file unchanged — and shouldn't burn a scheduled
    # slot. Retry with a different file (without replacement) a few times.
    remaining = list(candidates)
    last_err: GroundskeeperError | None = None
    for attempt in range(_MAX_FILE_ATTEMPTS):
        if not remaining:
            break
        chosen = random.choice(remaining)
        remaining.remove(chosen)
        path: str = chosen["path"]
        log.info(
            "executor.file_chosen",
            path=path,
            attempt=attempt + 1,
            size=int(chosen.get("size") or 0),
        )

        try:
            file_obj = github.get_file(token, owner, repo_name, path, branch)
        except UpstreamError as e:
            if e.code == "github_not_found":
                last_err = e
                log.info("executor.retry", reason="github_not_found", path=path)
                continue
            raise

        current_content = github.decode_file(file_obj)
        if current_content is None:
            last_err = GroundskeeperError(
                "Couldn't decode the file as UTF-8 — retrying with a different file.",
                code="decode_failed",
            )
            log.info("executor.retry", reason="decode_failed", path=path)
            continue

        commit_type = _choose_commit_type(cfg, current_content)
        try:
            new_content = _edit(commit_type, path, current_content, cfg["commit_style"])
        except UpstreamError as e:
            if e.code == "bedrock_truncated":
                last_err = e
                log.info("executor.retry", reason="bedrock_truncated", path=path)
                continue
            raise

        if new_content == current_content:
            last_err = GroundskeeperError(
                "Bedrock returned the file unchanged — retrying with a different file.",
                code="no_change",
            )
            log.info("executor.retry", reason="no_change", path=path)
            continue

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
            attempt=attempt + 1,
        )
        return {
            "status": "committed",
            "type": commit_type,
            "path": path,
            "sha": commit_meta.get("sha"),
            "commit_url": commit_meta.get("html_url"),
        }

    # All attempts retriable-failed; surface the last reason so it logs as a
    # real failure rather than an unhandled crash.
    if last_err is not None:
        raise last_err
    raise GroundskeeperError(
        "Ran out of eligible files to try after a few attempts.",
        code="no_files",
    )


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
        raise UpstreamError("Bedrock came back with an empty response.", code="bedrock_empty")
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


def _record_failure(event: dict, *, code: str, message: str) -> bool:
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
        return True
    except Exception as exc:
        # Surface — not swallow — the DDB failure so an outage doesn't leave
        # the original skip silently unrecorded.
        log.exception(
            "executor.log_failure_write_error",
            exc,
            original_code=code,
            original_message=message[:500],
            error=str(exc),
        )
        return False

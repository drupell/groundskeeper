"""Minimal GitHub REST client used by the API and the Executor Lambdas.

Uses only ``urllib`` so no extra Lambda-layer dependency is needed. Maps
HTTP errors onto the project's typed exceptions in ``shared.errors``.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from shared import log
from shared.errors import BadRequest, GroundskeeperError, Unauthorized, UpstreamError

_API = "https://api.github.com"
_UA = "groundskeeper-lambda/1.0"
_TIMEOUT_SECONDS = 15


def _request(
    method: str,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    url = path if path.startswith("http") else f"{_API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", _UA)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        # bandit B310: url is always f"{_API}{path}" where _API hardcodes
        # https://api.github.com; never user-controlled.
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # nosec B310
            raw = resp.read()
            payload: Any = json.loads(raw.decode()) if raw else {}
            return resp.status, _wrap(payload, resp.headers)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="ignore")
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            payload = {"message": body_text}
        message = payload.get("message", body_text) if isinstance(payload, dict) else body_text
        if e.code in (401, 403):
            raise Unauthorized(message, code="github_unauthorized") from e
        if e.code == 404:
            raise UpstreamError(message, code="github_not_found", status_code=404) from e
        if e.code in (301, 307, 308):
            # urllib follows redirects silently on GET/HEAD but raises here on
            # PUT/POST/DELETE. For our flow that almost always means the repo
            # was renamed or transferred — the caller should re-resolve via
            # the stable numeric repo id and retry. (307 = temporary redirect;
            # GitHub uses it for some rename-still-propagating cases.)
            raise UpstreamError(
                "The repo seems to have been renamed or moved on GitHub. "
                "Refresh the GitHub page in the dashboard to pick up the new name.",
                code="repo_moved",
                status_code=e.code,
            ) from e
        raise UpstreamError(f"GitHub returned {e.code}: {message}", code="github_error") from e
    except urllib.error.URLError as e:
        raise UpstreamError(f"Couldn't reach GitHub: {e.reason}", code="github_unreachable") from e


def _wrap(payload: Any, headers: Any) -> dict[str, Any]:
    header_map = {k: v for k, v in headers.items()}
    if isinstance(payload, list):
        return {"_list": payload, "_headers": header_map}
    if isinstance(payload, dict):
        payload["_headers"] = header_map
        return payload
    return {"_value": payload, "_headers": header_map}


# ---------------------------------------------------------------------------
# PAT verification + user info
# ---------------------------------------------------------------------------
def noreply_email(user_id: int | str | None, login: str | None) -> str | None:
    """GitHub's per-account privacy email.

    Commits authored with ``{id}+{login}@users.noreply.github.com`` (or the
    legacy ``{login}@users.noreply.github.com`` when the numeric id is
    unavailable) are attributed to that account and **count toward the
    contribution graph** — that's exactly what this address is for. Both are
    derivable from ``GET /user`` with no extra scope.
    """
    if not login:
        return None
    if user_id is not None and str(user_id):
        return f"{user_id}+{login}@users.noreply.github.com"
    return f"{login}@users.noreply.github.com"


def verify_token(token: str) -> dict[str, Any]:
    """Validate a PAT and return user info, scope list, and whether scopes are sufficient."""
    if not isinstance(token, str) or not token.strip():
        raise BadRequest("Paste a GitHub token first.")
    _, user = _request("GET", "/user", token)
    headers = user.get("_headers", {})
    raw_scopes = headers.get("X-OAuth-Scopes") or headers.get("x-oauth-scopes") or ""
    scopes = sorted({s.strip() for s in raw_scopes.split(",") if s.strip()})
    sufficient = "repo" in scopes or any(s.startswith("repo:") for s in scopes)

    login = user.get("login")
    uid = user.get("id")

    # A public profile email is rare; /user/emails needs the user:email scope
    # which a repo-only classic PAT lacks, so this is best-effort. Catch the
    # broad project base — the common 403 (missing scope) lands as Unauthorized,
    # not UpstreamError, and rejecting the whole token on it would block every
    # repo-only PAT during onboarding.
    email = user.get("email")
    if not email:
        try:
            _, payload = _request("GET", "/user/emails", token)
            emails = payload.get("_list", [])
            primary = next(
                (e for e in emails if e.get("primary") and e.get("verified")),
                None,
            )
            email = (primary or (emails[0] if emails else {}) or {}).get("email")
        except GroundskeeperError:
            email = None

    # The address commits MUST use to count on the graph: the real verified
    # email if we could get one, otherwise the account's noreply email.
    commit_email = email or noreply_email(uid, login)

    return {
        "id": uid,
        "login": login,
        "name": user.get("name") or login,
        "email": email,
        "commit_email": commit_email,
        "avatar_url": user.get("avatar_url"),
        "html_url": user.get("html_url"),
        "scopes": scopes,
        "sufficient": sufficient,
    }


# ---------------------------------------------------------------------------
# Repo metadata
# ---------------------------------------------------------------------------
def parse_repo_url(url: str) -> tuple[str, str]:
    """Extract ``(owner, repo)`` from a GitHub URL or ``owner/repo`` shorthand."""
    s = (url or "").strip()
    if not s:
        raise BadRequest("Paste a repository URL first.")

    # owner/repo shorthand
    if "://" not in s and not s.startswith("git@"):
        bare = s.removeprefix("github.com/").removesuffix(".git").strip("/")
        parts = bare.split("/")
        if len(parts) == 2 and all(parts):
            return parts[0], parts[1]

    if s.startswith("git@"):
        # git@github.com:owner/repo.git
        _, _, path = s.partition(":")
        bare = path.removesuffix(".git").strip("/")
        parts = bare.split("/")
        if len(parts) == 2 and all(parts):
            return parts[0], parts[1]
        raise BadRequest("Couldn't read an owner/repo out of that URL.")

    parsed = urllib.parse.urlparse(s)
    host = parsed.netloc or "github.com"
    if "github.com" not in host:
        raise BadRequest("Only github.com URLs work here.")
    path = parsed.path.strip("/").removesuffix(".git")
    parts = path.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise BadRequest("Couldn't read an owner/repo out of that URL.")
    return parts[0], parts[1]


def get_repo(token: str, owner: str, repo: str) -> dict[str, Any]:
    """Look up a repo by ``owner/name``.

    Note: ``urllib`` silently follows GitHub's 301 redirect on rename, so this
    call still succeeds against the old name and returns the *new* repo's
    details. Use ``id`` from the response to anchor future lookups.
    """
    _, payload = _request("GET", f"/repos/{owner}/{repo}", token)
    return _normalize_repo(payload)


def get_repo_by_id(token: str, repo_id: int) -> dict[str, Any]:
    """Look up a repo by its stable numeric id (survives renames + transfers)."""
    _, payload = _request("GET", f"/repositories/{repo_id}", token)
    return _normalize_repo(payload)


def _normalize_repo(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload["id"],
        "owner": payload["owner"]["login"],
        "name": payload["name"],
        "full_name": payload["full_name"],
        "html_url": payload["html_url"],
        "description": payload.get("description"),
        "default_branch": payload["default_branch"],
        "private": payload["private"],
        "permissions": payload.get("permissions", {}),
    }


def resolve_current(token: str, stored: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Reconcile a stored repo dict against GitHub's live state.

    Prefers looking up by the stable numeric ``id`` (survives renames and
    ownership transfers). Falls back to ``owner/name`` lookup for legacy
    configs that don't have a real id yet — those will be promoted on first
    read so subsequent calls anchor on the id.

    Returns ``(canonical, changed)`` where ``canonical`` is the merged repo
    dict the caller should use going forward, and ``changed`` is True when
    ``owner``, ``name``, ``full_name``, ``default_branch``, or ``id`` differ
    from the stored values. The caller is responsible for persisting
    ``canonical`` to DynamoDB when ``changed`` is True.
    """
    repo_id = stored.get("id")
    if not isinstance(repo_id, int) or repo_id <= 0:
        # Legacy: older configs stored "repo_id": "default" as a placeholder.
        legacy = stored.get("repo_id")
        repo_id = legacy if isinstance(legacy, int) and legacy > 0 else None

    if repo_id is not None:
        live = get_repo_by_id(token, repo_id)
    else:
        owner = stored.get("owner")
        name = stored.get("name")
        if not owner or not name:
            raise BadRequest("Repo isn't connected yet — connect it on the GitHub page first.")
        live = get_repo(token, owner, name)

    canonical = {
        **{k: v for k, v in stored.items() if k != "repo_id"},
        "id": live["id"],
        "owner": live["owner"],
        "name": live["name"],
        "full_name": live["full_name"],
        "default_branch": live["default_branch"],
        "url": live.get("html_url", stored.get("url")),
        "description": live.get("description", stored.get("description")),
        "private": live.get("private", stored.get("private")),
    }
    drift_keys = ("id", "owner", "name", "full_name", "default_branch")
    changed = any(stored.get(k) != canonical.get(k) for k in drift_keys)
    return canonical, changed


def get_last_commit(token: str, owner: str, repo: str, branch: str) -> dict[str, Any] | None:
    _, payload = _request("GET", f"/repos/{owner}/{repo}/commits?sha={branch}&per_page=1", token)
    items = payload.get("_list", [])
    if not items:
        return None
    c = items[0]
    return {
        "sha": c["sha"],
        "message": c["commit"]["message"].splitlines()[0][:200],
        "author": c["commit"]["author"]["name"],
        "date": c["commit"]["author"]["date"],
        "html_url": c.get("html_url"),
    }


# ---------------------------------------------------------------------------
# Contents API — used by the Executor
# ---------------------------------------------------------------------------
def list_tree(token: str, owner: str, repo: str, branch: str) -> list[dict[str, Any]]:
    """Return a flat list of tree entries (files + dirs) on the given branch."""
    _, payload = _request("GET", f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1", token)
    tree = payload.get("tree", [])
    # GitHub's /git/trees endpoint isn't paginable — a partial list is the best we can get.
    if payload.get("truncated"):
        log.warn("github tree truncated", owner=owner, repo=repo, branch=branch, entries=len(tree))
    return tree


def get_file(token: str, owner: str, repo: str, path: str, branch: str) -> dict[str, Any]:
    quoted_path = urllib.parse.quote(path)
    quoted_branch = urllib.parse.quote(branch)
    _, payload = _request(
        "GET",
        f"/repos/{owner}/{repo}/contents/{quoted_path}?ref={quoted_branch}",
        token,
    )
    return payload


def decode_file(file_obj: dict[str, Any]) -> str | None:
    """Decode a :func:`get_file` payload to text.

    Returns ``""`` for empty files or blobs GitHub won't inline (it sends
    ``encoding: "none"`` with empty content for files > 1 MB — the executor's
    size filter keeps us well under that, but be defensive). ``b64decode``
    with the default ``validate=False`` already discards the newlines GitHub
    interleaves into the base64. Returns None if the file isn't valid UTF-8.
    """
    if file_obj.get("encoding") != "base64":
        return ""
    content = file_obj.get("content") or ""
    if not content:
        return ""
    try:
        return base64.b64decode(content).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def put_file(
    token: str,
    owner: str,
    repo: str,
    path: str,
    branch: str,
    *,
    content: str,
    sha: str | None,
    message: str,
    author_name: str,
    author_email: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
        "committer": {"name": author_name, "email": author_email},
        "author": {"name": author_name, "email": author_email},
    }
    if sha:
        body["sha"] = sha
    quoted_path = urllib.parse.quote(path)
    _, payload = _request("PUT", f"/repos/{owner}/{repo}/contents/{quoted_path}", token, body)
    return payload

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

from shared.errors import BadRequest, Unauthorized, UpstreamError

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
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
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
        raise UpstreamError(f"GitHub returned {e.code}: {message}", code="github_error") from e
    except urllib.error.URLError as e:
        raise UpstreamError(f"Could not reach GitHub: {e.reason}", code="github_unreachable") from e


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
        raise BadRequest("Token is required.")
    _, user = _request("GET", "/user", token)
    headers = user.get("_headers", {})
    raw_scopes = headers.get("X-OAuth-Scopes") or headers.get("x-oauth-scopes") or ""
    scopes = sorted({s.strip() for s in raw_scopes.split(",") if s.strip()})
    sufficient = "repo" in scopes or any(s.startswith("repo:") for s in scopes)

    login = user.get("login")
    uid = user.get("id")

    # A public profile email is rare; /user/emails needs the user:email scope
    # which a repo-only classic PAT lacks, so this is best-effort.
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
        except UpstreamError:
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
        raise BadRequest("Repo URL is required.")

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
        raise BadRequest("Could not parse owner/repo from URL.")

    parsed = urllib.parse.urlparse(s)
    host = parsed.netloc or "github.com"
    if "github.com" not in host:
        raise BadRequest("Only github.com URLs are supported.")
    path = parsed.path.strip("/").removesuffix(".git")
    parts = path.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise BadRequest("Could not parse owner/repo from URL.")
    return parts[0], parts[1]


def get_repo(token: str, owner: str, repo: str) -> dict[str, Any]:
    _, payload = _request("GET", f"/repos/{owner}/{repo}", token)
    return {
        "owner": payload["owner"]["login"],
        "name": payload["name"],
        "full_name": payload["full_name"],
        "html_url": payload["html_url"],
        "description": payload.get("description"),
        "default_branch": payload["default_branch"],
        "private": payload["private"],
        "permissions": payload.get("permissions", {}),
    }


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
    return payload.get("tree", [])


def get_file(token: str, owner: str, repo: str, path: str, branch: str) -> dict[str, Any]:
    quoted_path = urllib.parse.quote(path)
    quoted_branch = urllib.parse.quote(branch)
    _, payload = _request(
        "GET",
        f"/repos/{owner}/{repo}/contents/{quoted_path}?ref={quoted_branch}",
        token,
    )
    return payload


def decode_file(file_obj: dict[str, Any]) -> str:
    """Decode a :func:`get_file` payload to text.

    Returns ``""`` for empty files or blobs GitHub won't inline (it sends
    ``encoding: "none"`` with empty content for files > 1 MB — the executor's
    size filter keeps us well under that, but be defensive). ``b64decode``
    with the default ``validate=False`` already discards the newlines GitHub
    interleaves into the base64.
    """
    if file_obj.get("encoding") != "base64":
        return ""
    content = file_obj.get("content") or ""
    if not content:
        return ""
    return base64.b64decode(content).decode("utf-8", errors="replace")


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

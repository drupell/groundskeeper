"""Amazon Bedrock (Nova Lite) helpers for commit content generation.

The executor calls :func:`generate_edit` with a system + user prompt produced by
:func:`creative_edit_prompt` or :func:`destructive_edit_prompt`; the model returns
the *entire* updated file. :func:`clean_output` strips the code fences models
occasionally wrap their answers in despite instructions not to.
"""

import json
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.errors import UpstreamError

_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
_client = boto3.client("bedrock-runtime")


def generate_edit(
    *,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
    temperature: float = 0.7,
) -> str:
    """Invoke Nova Lite and return the (cleaned) text response.

    Raises ``UpstreamError(code="bedrock_error")`` if the API call itself is
    rejected, and ``UpstreamError(code="bedrock_truncated")`` if the model hit
    its ``max_tokens`` output budget — committing the partial response would
    silently truncate the file in the user's repo, so the caller should retry
    with a different (smaller) file instead.
    """
    payload = {
        "messages": [{"role": "user", "content": [{"text": user_prompt}]}],
        "system": [{"text": system_prompt}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    try:
        resp = _client.invoke_model(
            modelId=_MODEL_ID,
            body=json.dumps(payload),
            contentType="application/json",
            accept="application/json",
        )
    except ClientError as e:
        raise UpstreamError(
            f"Bedrock didn't accept the request: {e.response['Error'].get('Message', e)}",
            code="bedrock_error",
        ) from e
    body = json.loads(resp["body"].read())
    if body.get("stopReason") == "max_tokens":
        raise UpstreamError(
            "The model hit its output limit — file may be too large to safely round-trip.",
            code="bedrock_truncated",
        )
    return clean_output(_extract_text(body))


def _extract_text(body: dict[str, Any]) -> str:
    out = body.get("output") or {}
    msg = out.get("message") or {}
    parts = msg.get("content") or []
    return "".join(p.get("text") or "" for p in parts if isinstance(p, dict))


def clean_output(text: str) -> str:
    """Strip leading/trailing whitespace and a single optional ``` fence."""
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def creative_edit_prompt(
    *,
    file_path: str,
    file_extension: str,
    file_text: str,
    style_prompt: str,
    min_lines: int,
    max_lines: int,
) -> tuple[str, str]:
    system = (
        "You produce small, plausible edits to source files for a personal automation "
        "project. Your reply is the FULL new file content — no commentary, no code "
        "fences, no surrounding prose. Preserve the original file's structure, "
        "indentation, and syntax. Add or revise content in the existing language and "
        "style — never introduce syntax errors. Stay within the requested line budget."
    )
    user = (
        f"FILE PATH: {file_path}\n"
        f"FILE TYPE: {file_extension or 'plain text'}\n"
        f"STYLE GUIDANCE: {style_prompt}\n"
        f"Add or revise between {min_lines} and {max_lines} lines of content.\n"
        f"Return the full updated file, nothing else.\n\n"
        f"--- CURRENT FILE ---\n{file_text}\n--- END ---"
    )
    return system, user


def destructive_edit_prompt(
    *,
    file_path: str,
    file_extension: str,
    file_text: str,
) -> tuple[str, str]:
    system = (
        "You make one small 'maintenance' edit to a source file. You decide which "
        "is more natural for this file: EITHER remove a few low-value lines "
        "(comments, blank lines, redundant or dead logic) OR rewrite/restructure a "
        "small existing section in place (simplify a block, tighten wording, rename "
        "a local). Whichever you pick, the edit must stay small, must NOT add new "
        "features, must NOT grow the file substantially, and must leave the file "
        "syntactically valid. Your reply is the FULL new file content — no "
        "commentary, no code fences, no surrounding prose."
    )
    user = (
        f"FILE PATH: {file_path}\n"
        f"FILE TYPE: {file_extension or 'plain text'}\n"
        f"Make one small maintenance edit — a deletion or an in-place rewrite, "
        f"your choice. Return the full updated file, nothing else.\n\n"
        f"--- CURRENT FILE ---\n{file_text}\n--- END ---"
    )
    return system, user

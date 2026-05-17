"""Tests for shared.github.decode_file — pure base64 payload decoding."""

import base64

from shared.github import decode_file


def _b64(text: str) -> str:
    # GitHub interleaves newlines every 60 chars; mimic that to prove they're
    # tolerated.
    raw = base64.b64encode(text.encode()).decode()
    return "\n".join(raw[i : i + 60] for i in range(0, len(raw), 60)) + "\n"


def test_decodes_base64_content_with_embedded_newlines():
    text = "def hi():\n    return 'hello world ' * 10\n"
    assert decode_file({"encoding": "base64", "content": _b64(text)}) == text


def test_empty_content_returns_empty_string():
    assert decode_file({"encoding": "base64", "content": ""}) == ""


def test_encoding_none_returns_empty_string():
    # GitHub's response for blobs > 1 MB.
    assert decode_file({"encoding": "none", "content": ""}) == ""


def test_missing_fields_return_empty_string():
    assert decode_file({}) == ""


def test_invalid_utf8_is_replaced_not_raised():
    payload = {"encoding": "base64", "content": base64.b64encode(b"\xff\xfe ok").decode()}
    out = decode_file(payload)
    assert out.endswith(" ok")

"""Tests for shared.github.parse_repo_url — the only pure helper in github."""

import pytest
from shared.errors import BadRequest
from shared.github import parse_repo_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://github.com/octocat/Hello-World", ("octocat", "Hello-World")),
        ("https://github.com/octocat/Hello-World.git", ("octocat", "Hello-World")),
        ("http://github.com/octocat/Hello-World/", ("octocat", "Hello-World")),
        ("github.com/octocat/Hello-World", ("octocat", "Hello-World")),
        ("octocat/Hello-World", ("octocat", "Hello-World")),
        ("git@github.com:octocat/Hello-World.git", ("octocat", "Hello-World")),
        ("  octocat/Hello-World  ", ("octocat", "Hello-World")),
    ],
)
def test_parses_supported_forms(raw, expected):
    assert parse_repo_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "https://gitlab.com/owner/repo", "https://github.com/onlyowner", "not a url"],
)
def test_rejects_bad_input(raw):
    with pytest.raises(BadRequest):
        parse_repo_url(raw)

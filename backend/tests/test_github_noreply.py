"""Tests for shared.github.noreply_email — the graph-attributable address."""

from shared.github import noreply_email


def test_id_prefixed_form_is_preferred():
    assert noreply_email(12345, "octocat") == "12345+octocat@users.noreply.github.com"


def test_string_id_is_accepted():
    assert noreply_email("123", "octocat") == "123+octocat@users.noreply.github.com"


def test_falls_back_to_login_only_without_id():
    assert noreply_email(None, "octocat") == "octocat@users.noreply.github.com"


def test_no_login_returns_none():
    assert noreply_email(123, None) is None
    assert noreply_email(None, None) is None

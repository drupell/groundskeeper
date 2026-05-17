"""Tests for shared.bedrock.clean_output — the only pure helper in bedrock."""

from shared.bedrock import clean_output


def test_plain_text_is_unchanged():
    assert clean_output("just some text\nline two") == "just some text\nline two"


def test_strips_whitespace():
    assert clean_output("  \n hello \n  ") == "hello"


def test_strips_a_bare_fence():
    fenced = "```\nprint('hi')\n```"
    assert clean_output(fenced) == "print('hi')"


def test_strips_a_language_tagged_fence():
    fenced = "```python\ndef f():\n    return 1\n```"
    assert clean_output(fenced) == "def f():\n    return 1"


def test_unbalanced_leading_fence_still_drops_first_line():
    # Models sometimes omit the closing fence; we still strip the opener.
    assert clean_output("```ts\nconst a = 1") == "const a = 1"

"""Tests for shared.config — schema defaults, validation, deep-merge."""

import copy

import pytest
from shared.config import _deep_merge, default_config, validate_config
from shared.errors import BadRequest


def test_default_config_is_valid():
    validate_config(default_config())  # must not raise


def test_rejects_non_dict():
    with pytest.raises(BadRequest):
        validate_config([])  # type: ignore[arg-type]


def test_rejects_gap_min_greater_than_max():
    c = default_config()
    c["gap"]["min_minutes"] = 200
    c["gap"]["max_minutes"] = 100
    with pytest.raises(BadRequest):
        validate_config(c)


def test_rejects_commit_count_min_greater_than_max():
    c = default_config()
    c["commit_count"]["min"] = 5
    c["commit_count"]["max"] = 2
    with pytest.raises(BadRequest):
        validate_config(c)


@pytest.mark.parametrize(
    "curve",
    [
        [[0.0, 1.0]],  # too few points
        [[0.0, 1.0], [1.5, 1.0]],  # x out of [0, 1]
        [[0.0, -1.0], [1.0, 1.0]],  # negative y
        [[0.0, 1.0], [1.0]],  # point not a pair
    ],
)
def test_rejects_bad_curves(curve):
    c = default_config()
    c["commit_count"]["curve"] = curve
    with pytest.raises(BadRequest):
        validate_config(c)


def test_rejects_missing_day():
    c = default_config()
    del c["schedule"]["monday"]
    with pytest.raises(BadRequest):
        validate_config(c)


def test_rejects_bad_time_string():
    c = default_config()
    c["schedule"]["monday"]["start"] = "9am"
    with pytest.raises(BadRequest):
        validate_config(c)


def test_rejects_out_of_range_destructive_probability():
    c = default_config()
    c["commit_style"]["destructive_probability"] = 1.5
    with pytest.raises(BadRequest):
        validate_config(c)


def test_rejects_min_lines_greater_than_max_lines():
    c = default_config()
    c["commit_style"]["min_lines"] = 10
    c["commit_style"]["max_lines"] = 3
    with pytest.raises(BadRequest):
        validate_config(c)


def test_deep_merge_is_recursive_and_non_destructive():
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    overlay = {"nested": {"y": 9, "z": 3}, "b": 2}
    merged = _deep_merge(base, overlay)
    assert merged == {"a": 1, "b": 2, "nested": {"x": 1, "y": 9, "z": 3}}
    # Original inputs are untouched.
    assert base == {"a": 1, "nested": {"x": 1, "y": 2}}


def test_deep_merge_overlay_scalar_replaces_dict():
    merged = _deep_merge({"k": {"deep": 1}}, {"k": "scalar"})
    assert merged == {"k": "scalar"}


def test_validate_accepts_a_realistic_patch_result():
    c = default_config()
    patched = _deep_merge(copy.deepcopy(c), {"timezone": "America/Chicago"})
    validate_config(patched)
    assert patched["timezone"] == "America/Chicago"

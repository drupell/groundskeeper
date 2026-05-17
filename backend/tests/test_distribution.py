"""Tests for shared.distribution — the curve sampler the orchestrator uses."""

import random

import pytest
from shared.distribution import expected_value, sample_count

UNIFORM = [[0.0, 1.0], [0.5, 1.0], [1.0, 1.0]]
PEAK_HIGH = [[0.0, 0.0], [1.0, 1.0]]
PEAK_LOW = [[0.0, 1.0], [1.0, 0.0]]


def test_single_value_range_returns_that_value():
    assert sample_count(3, 3, UNIFORM) == 3


def test_samples_stay_within_bounds():
    random.seed(1)
    for _ in range(500):
        n = sample_count(0, 4, UNIFORM)
        assert 0 <= n <= 4
        assert isinstance(n, int)


def test_max_less_than_min_raises():
    with pytest.raises(ValueError):
        sample_count(5, 2, UNIFORM)


def test_uniform_curve_is_roughly_flat():
    random.seed(42)
    counts = {n: 0 for n in range(5)}
    for _ in range(20_000):
        counts[sample_count(0, 4, UNIFORM)] += 1
    # Every bucket should be within ~25% of the 1/5 expectation.
    for c in counts.values():
        assert 0.15 < c / 20_000 < 0.25


def test_skewed_curve_biases_toward_the_weighted_end():
    random.seed(7)
    high = sum(sample_count(0, 4, PEAK_HIGH) for _ in range(5_000)) / 5_000
    low = sum(sample_count(0, 4, PEAK_LOW) for _ in range(5_000)) / 5_000
    assert high > 2.5  # mass piled on the high end
    assert low < 1.5  # mass piled on the low end


def test_zero_weight_curve_falls_back_to_uniform():
    random.seed(0)
    flat_zero = [[0.0, 0.0], [1.0, 0.0]]
    seen = {sample_count(0, 3, flat_zero) for _ in range(200)}
    # Should still produce values across the range rather than crashing.
    assert seen.issubset({0, 1, 2, 3})
    assert len(seen) > 1


def test_expected_value_of_uniform_is_the_midpoint():
    assert expected_value(0, 4, UNIFORM) == pytest.approx(2.0, abs=1e-6)


def test_expected_value_equal_bounds():
    assert expected_value(2, 2, UNIFORM) == 2.0

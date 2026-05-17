"""Tests for shared.timeplan — commit-time placement with gap clamping."""

import random
from datetime import datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

from shared.timeplan import plan_times

TZ = ZoneInfo("UTC")
DAY = datetime(2026, 5, 20, tzinfo=TZ)


def _plan(n, start, end, gap_min=15, gap_max=120, seed=0):
    return plan_times(
        local_date=DAY,
        tz=TZ,
        start_hhmm=start,
        end_hhmm=end,
        n=n,
        gap_min_minutes=gap_min,
        gap_max_minutes=gap_max,
        rng=random.Random(seed),
    )


def test_zero_or_negative_count_returns_empty():
    assert _plan(0, "09:00", "17:00") == ([], 0)
    assert _plan(-3, "09:00", "17:00") == ([], 0)


def test_end_before_start_returns_empty():
    assert _plan(3, "17:00", "09:00") == ([], 0)


def test_single_commit_lands_inside_window():
    times, placed = _plan(1, "09:00", "17:00")
    assert placed == 1
    t = times[0]
    assert DAY.replace(hour=9) <= t <= DAY.replace(hour=17)


def test_times_are_sorted_and_within_window():
    times, placed = _plan(4, "09:00", "18:00")
    assert placed == 4
    assert times == sorted(times)
    for t in times:
        assert DAY.replace(hour=9) <= t <= DAY.replace(hour=18)


def test_minimum_gap_is_respected_when_feasible():
    times, placed = _plan(4, "09:00", "18:00", gap_min=30, gap_max=90)
    assert placed == 4
    for earlier, later in pairwise(times):
        gap = (later - earlier).total_seconds() / 60
        assert gap >= 30 - 1e-6


def test_tight_window_clamps_count():
    # 60-minute window, 30-min minimum gap → at most 3 commits fit.
    _times, placed = _plan(8, "09:00", "10:00", gap_min=30, gap_max=45)
    assert placed <= 3
    assert placed >= 1

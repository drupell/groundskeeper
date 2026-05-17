"""Place N random commit times in a daily window respecting gap constraints.

The orchestrator passes a local-day window (``[start, end]`` in the user's
configured timezone) and asks for ``n`` timestamps inside it with gaps in
``[gap_min_minutes, gap_max_minutes]``. If the window is too tight to fit
``n`` commits with the minimum gap, we *clamp* by reducing ``n`` until it
fits and report how many we actually placed.
"""

import random
from datetime import datetime, time, timedelta, tzinfo


def _at(local_date: datetime, hhmm: str, tz: tzinfo) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(local_date.date(), time(h, m), tzinfo=tz)


def plan_times(
    *,
    local_date: datetime,
    tz: tzinfo,
    start_hhmm: str,
    end_hhmm: str,
    n: int,
    gap_min_minutes: int,
    gap_max_minutes: int,
    rng: random.Random | None = None,
) -> tuple[list[datetime], int]:
    """Return ``(times, placed)`` — sorted tz-aware datetimes and how many we placed.

    ``placed`` may be less than ``n`` if the window was too tight. Caller is
    responsible for filtering out any times that have already passed (e.g.
    when the orchestrator runs for a partial-day window).
    """
    r = rng or random
    start = _at(local_date, start_hhmm, tz)
    end = _at(local_date, end_hhmm, tz)
    if end <= start or n <= 0:
        return [], 0

    total_minutes = (end - start).total_seconds() / 60.0
    if gap_min_minutes > 0 and n > 1:
        max_by_gap = int(total_minutes // gap_min_minutes) + 1
        n = min(n, max_by_gap)
    if n <= 0:
        return [], 0
    if n == 1:
        offset = r.uniform(0, total_minutes)
        return [start + timedelta(minutes=offset)], 1

    # Try a few times to draw random gaps in [gap_min, gap_max] that fit.
    for _ in range(32):
        gaps = [r.uniform(gap_min_minutes, gap_max_minutes) for _ in range(n - 1)]
        gap_sum = sum(gaps)
        if gap_sum <= total_minutes:
            head_max = total_minutes - gap_sum
            offset = r.uniform(0, head_max)
            times = [start + timedelta(minutes=offset)]
            for g in gaps:
                times.append(times[-1] + timedelta(minutes=g))
            return times, n

    # Fallback: evenly space across the window.
    step = total_minutes / (n - 1)
    times = [start + timedelta(minutes=i * step) for i in range(n)]
    return times, n

"""Sample integer commit counts from a user-shaped probability curve.

The dashboard's curve editor stores its shape as a list of ``[x_norm, y]``
control points where ``x_norm`` ∈ [0, 1] spans the configured ``[min, max]``
count range and ``y`` is a non-negative relative weight. ``sample_count``
linearly interpolates the curve at each integer position and draws once from
the resulting categorical distribution.
"""

import bisect
import random


def sample_count(min_count: int, max_count: int, curve: list[list[float]]) -> int:
    if max_count < min_count:
        raise ValueError("max_count must be ≥ min_count")
    if max_count == min_count:
        return min_count

    pts = sorted(((float(x), float(y)) for x, y in curve), key=lambda p: p[0])
    if not pts:
        pts = [(0.0, 1.0), (1.0, 1.0)]
    if pts[0][0] > 0.0:
        pts.insert(0, (0.0, pts[0][1]))
    if pts[-1][0] < 1.0:
        pts.append((1.0, pts[-1][1]))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    span = max_count - min_count
    weights: list[float] = []
    for n in range(min_count, max_count + 1):
        weights.append(_interp(xs, ys, (n - min_count) / span))
    if sum(weights) <= 0:
        weights = [1.0] * len(weights)
    return random.choices(range(min_count, max_count + 1), weights=weights, k=1)[0]


def expected_value(min_count: int, max_count: int, curve: list[list[float]]) -> float:
    """Return the analytic expected value of the curve over [min, max]."""
    if max_count == min_count:
        return float(min_count)
    pts = sorted(((float(x), float(y)) for x, y in curve), key=lambda p: p[0])
    if not pts:
        pts = [(0.0, 1.0), (1.0, 1.0)]
    if pts[0][0] > 0.0:
        pts.insert(0, (0.0, pts[0][1]))
    if pts[-1][0] < 1.0:
        pts.append((1.0, pts[-1][1]))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    span = max_count - min_count
    counts = list(range(min_count, max_count + 1))
    weights = [_interp(xs, ys, (n - min_count) / span) for n in counts]
    total = sum(weights)
    if total <= 0:
        return (min_count + max_count) / 2
    return sum(n * w for n, w in zip(counts, weights, strict=True)) / total


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    if x <= xs[0]:
        return max(0.0, ys[0])
    if x >= xs[-1]:
        return max(0.0, ys[-1])
    idx = bisect.bisect_left(xs, x)
    x0, x1 = xs[idx - 1], xs[idx]
    y0, y1 = ys[idx - 1], ys[idx]
    span = x1 - x0
    if span == 0:
        return max(0.0, y0)
    t = (x - x0) / span
    return max(0.0, y0 + t * (y1 - y0))

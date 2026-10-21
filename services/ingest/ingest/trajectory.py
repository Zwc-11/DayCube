"""Trajectory simplification that keeps time.

The viewer moves the character by interpolating the path linearly in time. Douglas-Peucker with
the perpendicular distance would delete a long pause on a straight corridor, and the character
would glide through it. This uses the synchronized Euclidean distance instead (TD-TR; Meratnia
and de By, 2004). For each original fix it measures the distance to where the simplified path
places the person *at that fix's time*. So at every original timestamp the simplified path is
within ``tolerance`` of the original, and between timestamps both paths are straight lines.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

Point = dict[str, Any]
# A change in any of these must survive simplification: they select floors and render styles.
ATTRIBUTES = ("floor", "fidelity", "pos_conf")


def simplify(points: Sequence[Point], tolerance: float) -> list[Point]:
    """Points need numeric ``t`` and nullable ``x``/``y``, in time order. Returns a subsequence.

    Keeps the first and last point, every point with an unknown position, and both sides of every
    change in :data:`ATTRIBUTES`. Simplifies each run between those breakpoints independently.
    """
    n = len(points)
    if n <= 2:
        return list(points)
    keep = bytearray(n)
    keep[0] = keep[-1] = 1
    run_start = 0
    for i in range(1, n):
        if _breaks(points[i - 1], points[i]):
            keep[i - 1] = keep[i] = 1
            _mark(points, run_start, i - 1, tolerance, keep)
            run_start = i
    _mark(points, run_start, n - 1, tolerance, keep)
    return [point for point, kept in zip(points, keep, strict=True) if kept]


def _breaks(previous: Point, current: Point) -> bool:
    if previous["x"] is None or current["x"] is None or previous["y"] is None or current["y"] is None:
        return True
    return any(previous.get(name) != current.get(name) for name in ATTRIBUTES)


def _mark(points: Sequence[Point], first: int, last: int, tolerance: float, keep: bytearray) -> None:
    """Iterative Douglas-Peucker over points[first..last] with the synchronized distance."""
    stack = [(first, last)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        a, b = points[lo], points[hi]
        ta, xa, ya = a["t"], a["x"], a["y"]
        span = b["t"] - ta
        dx, dy = b["x"] - xa, b["y"] - ya
        worst, worst_at = tolerance, -1
        for i in range(lo + 1, hi):
            p = points[i]
            r = (p["t"] - ta) / span if span > 0 else 0.0
            distance = math.hypot(p["x"] - (xa + r * dx), p["y"] - (ya + r * dy))
            if distance > worst:
                worst, worst_at = distance, i
        if worst_at >= 0:
            keep[worst_at] = 1
            stack.append((lo, worst_at))
            stack.append((worst_at, hi))

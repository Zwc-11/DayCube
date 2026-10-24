from dataclasses import dataclass


@dataclass(frozen=True)
class Fixation:
    x: float
    y: float
    start: float
    duration: float
    confidence: float


def attention(
    bbox: tuple[float, float, float, float],
    fixations: list[Fixation],
    interval: tuple[float, float],
    *,
    margin: float = 40,
) -> float:
    """Clip dwell to the observation interval; merge overlaps so samples cannot double count."""
    x0, y0, x1, y1 = bbox
    start, end = interval
    if end <= start:
        raise ValueError("Observation interval must have positive duration")
    ranges = sorted(
        (max(start, fix.start), min(end, fix.start + fix.duration))
        for fix in fixations
        if fix.confidence >= 0.5
        and x0 - margin <= fix.x <= x1 + margin
        and y0 - margin <= fix.y <= y1 + margin
        and fix.start < end
        and fix.start + fix.duration > start
    )
    dwell = 0.0
    last_end = start
    for left, right in ranges:
        dwell += max(0, right - max(left, last_end))
        last_end = max(last_end, right)
    return min(1, dwell / max(end - start, 0.5))

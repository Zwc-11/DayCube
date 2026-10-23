"""Which floor a person is on, from barometric altitude, when GPS has no fix indoors.

Air pressure falls about 12 Pa per metre near sea level, so a phone or watch barometer resolves a
single floor. Weather moves the same signal by up to about 1 hPa an hour, which looks like 8.5 m
of climbing. The spec's first design compared altitude with the visit's entry level, so over a
long visit weather alone would report floors never climbed.

So this integrates only *vertical movement*. A floor change is fast: stairs climb about
0.2 m/s, escalators 0.25 m/s, elevators 1-3 m/s. Weather drift is about 0.002 m/s. Altitude
change is counted only inside stretches where the filtered vertical speed exceeds
:data:`MOVING_MPS`, and drift between them is ignored. Steps, applied to each visit separately:

1. Median-filter altitude over 15 s to remove door and elevator pressure spikes.
2. Find movement stretches; join stretches less than 10 s apart, so a transient that goes up and
   back down nets to zero.
3. Sum each stretch's net climb into a level relative to the entrance.
4. Change floor only when the level is more than 0.6 floor heights from the current floor
   (hysteresis), and then to the nearest floor.
"""

from __future__ import annotations

import math
from bisect import bisect_left, insort
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MEDIAN_WINDOW_S = 15.0
SPEED_WINDOW_S = 10.0
MOVING_MPS = 0.05  # 20x a fast weather change; a 5% parking ramp at walking pace is 0.07
MERGE_GAP_S = 10.0
HYSTERESIS = 0.6  # of a floor height
DEFAULT_FLOOR_HEIGHT_M = 3.3
FLIGHTS_WINDOW_S = 90.0
FLIGHTS_BONUS = 0.15
UNKNOWN_HEIGHT_CAP = 0.75  # without a venue pack or OSM height, the floor count is a guess
MAX_CONFIDENCE = 0.98


@dataclass(frozen=True, slots=True)
class FloorChange:
    t: float  # seconds, start of the movement
    t_end: float
    start: int
    end: int
    climb_m: float
    confidence: float


@dataclass(frozen=True, slots=True)
class FloorTrack:
    floors: tuple[int, ...]  # one per input sample
    confidence: tuple[float, ...]
    changes: tuple[FloorChange, ...]


def altitude_from_pressure(pressure_hpa: Sequence[float], reference_hpa: float | None = None) -> list[float]:
    """Relative altitude in metres (standard-atmosphere barometric formula). Only differences
    matter, so the reference defaults to the first sample."""
    if not pressure_hpa:
        return []
    p0 = reference_hpa or pressure_hpa[0]
    return [44330.8 * (1.0 - (p / p0) ** (1 / 5.255)) for p in pressure_hpa]


def floor_height(venue: dict[str, Any] | None = None, osm_tags: dict[str, str] | None = None) -> tuple[float, bool]:
    """Storey height and whether it is known: venue pack, then OSM ``height``/``building:levels``,
    then the spec's 3.3 m default. Malls often run 4.5-6 m, so the default under-counts there."""
    if venue and venue.get("floor_height_m"):
        return float(venue["floor_height_m"]), True
    tags = osm_tags or {}
    try:
        height, levels = float(tags["height"].removesuffix(" m")), int(tags["building:levels"])
        if height > 0 and levels > 0:
            return height / levels, True
    except (KeyError, ValueError):
        pass
    return DEFAULT_FLOOR_HEIGHT_M, False


def estimate_floors(
    times: Sequence[float],
    altitudes: Sequence[float],
    height_m: float = DEFAULT_FLOOR_HEIGHT_M,
    *,
    height_known: bool = True,
    start_floor: int = 0,
    flights: Sequence[float] = (),
) -> FloorTrack:
    """Floors for one visit. ``times`` are seconds in increasing order; ``altitudes`` metres on
    any datum. ``start_floor`` is the entrance level (0 unless a venue pack says otherwise).
    ``flights`` are times of Apple Health "flights climbed" samples, which corroborate changes."""
    n = len(times)
    if n != len(altitudes):
        raise ValueError("times and altitudes must have the same length")
    if any(b < a for a, b in zip(times, times[1:], strict=False)):
        raise ValueError("times must be in increasing order")
    if height_m <= 0:
        raise ValueError("floor height must be positive")
    if n == 0:
        return FloorTrack((), (), ())
    smooth = _median_filter(times, altitudes, MEDIAN_WINDOW_S)
    cap = MAX_CONFIDENCE if height_known else UNKNOWN_HEIGHT_CAP
    flight_times = sorted(flights)

    def closeness(level: float, floor: int) -> float:
        residual = abs(level - (floor - start_floor) * height_m)
        return min(cap, max(0.0, 1.0 - residual / (0.5 * height_m)))

    floors: list[int] = []
    confidence: list[float] = []
    changes: list[FloorChange] = []
    floor, level = start_floor, 0.0
    for first, last in _movement(times, smooth):
        # Until a movement completes, the person is on the floor they started from.
        floors.extend([floor] * (last - len(floors)))
        confidence.extend([closeness(level, floor)] * (last - len(confidence)))
        climb = smooth[last] - smooth[first]
        level += climb
        target = start_floor + round(level / height_m)
        if target != floor and abs(level - (floor - start_floor) * height_m) > HYSTERESIS * height_m:
            score = closeness(level, target) + (FLIGHTS_BONUS if _near(flight_times, times[first], times[last]) else 0)
            changes.append(FloorChange(times[first], times[last], floor, target, climb, min(cap, score)))
            floor = target
    floors.extend([floor] * (n - len(floors)))
    confidence.extend([closeness(level, floor)] * (n - len(confidence)))
    return FloorTrack(tuple(floors), tuple(confidence), tuple(changes))


def _median_filter(times: Sequence[float], values: Sequence[float], window: float) -> list[float]:
    """Centered running median over ``window`` seconds: a sorted window, O(n * w)."""
    half, out, ordered = window / 2, [], []
    lo = hi = 0
    for t in times:
        while hi < len(times) and times[hi] <= t + half:
            insort(ordered, values[hi])
            hi += 1
        while times[lo] < t - half:
            del ordered[bisect_left(ordered, values[lo])]
            lo += 1
        m = len(ordered)
        out.append(ordered[m // 2] if m % 2 else (ordered[m // 2 - 1] + ordered[m // 2]) / 2)
    return out


def _movement(times: Sequence[float], smooth: Sequence[float]) -> list[tuple[int, int]]:
    """Index ranges where vertical speed over the trailing window exceeds MOVING_MPS, each
    widened to the start of its window and joined across gaps shorter than MERGE_GAP_S."""
    stretches: list[tuple[int, int]] = []
    back = 0
    for i, t in enumerate(times):
        while times[back] < t - SPEED_WINDOW_S:
            back += 1
        span = t - times[back]
        if span <= 0 or abs(smooth[i] - smooth[back]) / span < MOVING_MPS:
            continue
        if stretches and times[back] - times[stretches[-1][1]] < MERGE_GAP_S:
            stretches[-1] = (stretches[-1][0], i)
        else:
            stretches.append((back, i))
    return stretches


def _near(sorted_times: Sequence[float], t0: float, t1: float) -> bool:
    i = bisect_left(sorted_times, t0 - FLIGHTS_WINDOW_S)
    return i < len(sorted_times) and sorted_times[i] <= t1 + FLIGHTS_WINDOW_S


def floor_events(
    track: FloorTrack, *, place: str, iso: Any, source_ref: str, fidelity: str = "estimated"
) -> list[dict[str, Any]]:
    """``place.floor`` events for each change. ``iso`` maps seconds to an ISO 8601 timestamp."""
    events = []
    for index, change in enumerate(track.changes):
        direction = "Up" if change.end > change.start else "Down"
        events.append(
            {
                "id": f"ev_floor_{place}_{index}",
                "t": iso(change.t),
                "t_end": iso(change.t_end),
                "type": "place.floor",
                "label": f"{direction} to floor {change.end}",
                "place": place,
                "floor": change.end,
                "x": None,
                "y": None,
                "source": {"kind": "barometer", "ref": source_ref},
                "confidence": round(change.confidence, 3),
                "fidelity": fidelity,
                "text": f"{change.climb_m:+.1f} m",
                "conf": round(change.confidence, 3),
            }
        )
    return events


def naive_floors(altitudes: Sequence[float], height_m: float, settle: int = 20) -> list[int]:
    """The entry-referenced estimate: round((altitude - entry level) / height). Kept only to show
    in tests what weather drift does to it; not used by the pipeline."""
    base = math.fsum(altitudes[:settle]) / min(settle, len(altitudes))
    return [round((a - base) / height_m) for a in altitudes]

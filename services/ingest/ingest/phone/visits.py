"""Where a phone recording stayed and how it moved between: visits and transits.

A day is a sequence of stays joined by trips. The phone's motion activity says how it moved
(Core Motion on iOS, Activity Recognition on Android: stationary, walking, running, cycling,
automotive); GPS alone confuses a slow drive in traffic with a walk and jitter with movement.

1. **Trips.** Each second of the recording takes the activity last reported ("unknown" carries
   nothing). Over a sliding minute, the most common known activity decides: driving or cycling for
   two minutes or more is a trip. Without activity, ground speed decides (a minute's median over
   6 m/s: driving). A stop of less than ``JOIN_S`` inside a drive (a light, a queue) stays part of it.
2. **Stays.** What lies between trips is a stay. A stay is split where the phone was still for five
   minutes or more at two places over 250 m apart, the walk between them becoming a trip on foot.
3. **Kinds.** A stay mostly on foot and wider than 75 m is an outdoor visit (a walk, a park); the
   stay the recording begins or ends at is home when it begins and ends at the same place
   ("house"); any other stay is a building ("venue").

Each visit has its median position, its fixes and the activity's agreement as confidence.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ..skeleton.parsers.types import Fix
from .geo import metres
from .sensor_logger import Recording

MODES = {"automotive": "vehicle", "cycling": "bike", "walking": "foot", "running": "foot", "stationary": "still"}
WINDOW_S = 60
MIN_TRIP_S = 120.0
JOIN_S = 180.0
MIN_STAY_S = 60.0
STILL_SPLIT_S = 300.0
SPLIT_M = 250.0
OUTDOOR_FOOT = 0.4
OUTDOOR_M = 75.0
HOME_M = 150.0
VEHICLE_MPS = 6.0


@dataclass
class Span:
    kind: str  # "stay" or "trip"
    t0: float  # epoch seconds
    t1: float
    mode: str = "unknown"  # a trip's: vehicle, bike, walk; a stay's place kind once known
    conf: float = 0.5
    fixes: list[Fix] = field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    foot: float = 0.0  # share of the stay's known seconds on foot
    extent_m: float = 0.0  # 90th percentile distance of its fixes from its middle
    distance_m: float = 0.0  # path length (trips)
    steps: int | None = None


def segment(recording: Recording) -> list[Span]:
    """Stays and trips, in time order, covering the recording."""
    t0, t1 = int(recording.t0), int(recording.t1) + 1
    labels = _labels(recording, t0, t1)
    trips = _trips(labels, t0)
    spans: list[Span] = []
    cursor = float(recording.t0)
    for a, b, mode, conf in trips:
        if spans and spans[-1].kind == "trip" and a - cursor < MIN_STAY_S:
            spans[-1].t1 = b  # a stop too short to be a stay: the same trip
        else:
            spans.append(Span("stay", cursor, a))
            spans.append(Span("trip", a, b, mode, conf))
        cursor = b
    if recording.t1 - cursor > 0:
        spans.append(Span("stay", cursor, float(recording.t1)))
    spans = [s for s in spans if s.t1 > s.t0]
    out: list[Span] = []
    for span in spans:
        span.fixes = [f for f in recording.fixes if span.t0 <= f.t.timestamp() <= span.t1]
        out += _split(span, labels, t0) if span.kind == "stay" else [span]
    for span in out:
        _describe(span, labels, t0, recording)
    _kinds([s for s in out if s.kind == "stay"], recording)
    return out


def _labels(recording: Recording, t0: int, t1: int) -> list[str | None]:
    """The mode of each second (None: not known)."""
    labels: list[str | None] = [None] * (t1 - t0)
    if recording.activity:
        current: str | None = None
        events = iter(recording.activity)
        nxt = next(events, None)
        for k in range(len(labels)):
            while nxt is not None and nxt[0] <= t0 + k:
                current = MODES.get(nxt[1])
                nxt = next(events, None)
            labels[k] = current
        return labels
    from .sensor_logger import speeds

    for fix, v in zip(recording.fixes, speeds(recording.fixes), strict=True):
        k = int(fix.t.timestamp()) - t0
        if 0 <= k < len(labels):
            labels[k] = "fast" if v > VEHICLE_MPS else "slow"
    # Fill the seconds between fixes; a minute's median speed class decides below.
    last = None
    for k, label in enumerate(labels):
        labels[k] = last = label if label is not None else last
    return ["vehicle" if label == "fast" else "foot" if label == "slow" else None for label in labels]


def _trips(labels: list[str | None], t0: int) -> list[tuple[float, float, str, float]]:
    """(start, end, mode, agreement) of each drive or ride."""
    n, half = len(labels), WINDOW_S // 2
    counts: dict[str, int] = {}
    smooth: list[str | None] = []
    window: list[str | None] = []
    # A centred running majority over the known labels.
    for k in range(min(half, n)):
        window.append(labels[k])
        if labels[k]:
            counts[labels[k]] = counts.get(labels[k], 0) + 1
    for k in range(n):
        if k + half < n and labels[k + half]:
            counts[labels[k + half]] = counts.get(labels[k + half], 0) + 1
        if k - half - 1 >= 0 and labels[k - half - 1]:
            counts[labels[k - half - 1]] -= 1
        smooth.append(max(counts, key=lambda m: counts[m]) if any(counts.values()) else None)
    runs: list[list] = []
    for k, mode in enumerate(smooth):
        moving = mode if mode in ("vehicle", "bike") else None
        if moving and runs and runs[-1][2] == moving and k - runs[-1][1] <= JOIN_S:
            runs[-1][1] = k + 1
        elif moving:
            runs.append([k, k + 1, moving])
    trips = []
    for a, b, mode in runs:
        if b - a < MIN_TRIP_S:
            continue
        known = [m for m in labels[a:b] if m]
        agree = sum(m == mode for m in known) / len(known) if known else 0.5
        trips.append((float(t0 + a), float(t0 + b), "vehicle" if mode == "vehicle" else "bike", round(agree, 3)))
    return trips


def _split(span: Span, labels: list[str | None], t0: int) -> list[Span]:
    """A stay with still periods at places far apart: stay, walk, stay."""
    stills: list[tuple[float, float]] = []
    start = None
    for k in range(int(span.t0) - t0, min(int(span.t1) - t0, len(labels))):
        if labels[k] == "still":
            start = k if start is None else start
        elif start is not None:
            stills.append((t0 + start, t0 + k))
            start = None
    if start is not None:
        stills.append((t0 + start, span.t1))
    places = []
    for a, b in stills:
        if b - a < STILL_SPLIT_S:
            continue
        here = [f for f in span.fixes if a <= f.t.timestamp() <= b]
        if here:
            places.append((a, b, statistics.median(f.lat for f in here), statistics.median(f.lon for f in here)))
    out, cursor = [], span.t0
    for (_a0, b0, la0, lo0), (a1, _b1, la1, lo1) in zip(places, places[1:], strict=False):
        if metres(la0, lo0, la1, lo1) > SPLIT_M:
            out.append(Span("stay", cursor, b0))
            out.append(Span("trip", b0, a1, "walk", 0.7))
            cursor = a1
    out.append(Span("stay", cursor, span.t1))
    for piece in out:
        piece.fixes = [f for f in span.fixes if piece.t0 <= f.t.timestamp() <= piece.t1]
    return out


def _describe(span: Span, labels: list[str | None], t0: int, recording: Recording) -> None:
    fixes = span.fixes
    if fixes:
        span.lat = statistics.median(f.lat for f in fixes)
        span.lon = statistics.median(f.lon for f in fixes)
        far = sorted(metres(span.lat, span.lon, f.lat, f.lon) for f in fixes)
        span.extent_m = far[int(0.9 * (len(far) - 1))]
        span.distance_m = sum(metres(a.lat, a.lon, b.lat, b.lon) for a, b in zip(fixes, fixes[1:], strict=False))
    known = [m for m in labels[max(int(span.t0) - t0, 0) : max(int(span.t1) - t0, 0)] if m]
    span.foot = sum(m == "foot" for m in known) / len(known) if known else 0.0
    steps = [s for t, s in recording.steps if span.t0 <= t <= span.t1]
    span.steps = steps[-1] - steps[0] if len(steps) >= 2 else None
    if span.kind == "stay":
        span.conf = round(min(0.95, 0.5 + 0.45 * min(len(fixes), 30) / 30), 3)


def _kinds(stays: list[Span], recording: Recording) -> None:
    if not stays:
        return
    first, last = stays[0], stays[-1]
    home = (
        first is not last and first.lat is not None and last.lat is not None
        and metres(first.lat, first.lon, last.lat, last.lon) <= HOME_M
    )  # fmt: skip
    for stay in stays:
        if stay.foot >= OUTDOOR_FOOT and stay.extent_m >= OUTDOOR_M:
            stay.mode = "outdoor"
        elif home and stay.lat is not None and metres(stay.lat, stay.lon, first.lat, first.lon) <= HOME_M:
            stay.mode = "house"
        else:
            stay.mode = "venue"

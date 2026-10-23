"""Floor estimation on synthetic barometer traces with known ground truth (spec 3.7)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from ingest.contracts import validate
from ingest.skeleton.floors import (
    altitude_from_pressure,
    estimate_floors,
    floor_events,
    floor_height,
    naive_floors,
)

H = 3.3


def trace(
    plan: list[tuple[str, float, float]], *, noise: float = 0.1, drift_mps: float = 0.0, seed: int = 1
) -> tuple[list[float], list[float], list[int]]:
    """1 Hz samples. ``plan`` items: ("dwell", seconds, 0), ("move", seconds, metres) or
    ("spike", seconds, metres), a pressure transient that returns to the previous level."""
    rng = random.Random(seed)
    times, alts, truth, level, t = [], [], [], 0.0, 0.0
    for kind, seconds, metres in plan:
        for k in range(int(seconds)):
            if kind == "move":
                value = level + metres * (k + 1) / seconds
            elif kind == "spike":
                value = level + metres
            else:
                value = level
            times.append(t)
            alts.append(value + drift_mps * t + rng.gauss(0, noise))
            truth.append(round((value if kind != "spike" else level) / H))
            t += 1
        if kind == "move":
            level += metres
    return times, alts, truth


def test_two_floor_climb_with_an_elevator_transient() -> None:
    times, alts, _ = trace(
        [("dwell", 120, 0), ("spike", 3, 1.6), ("dwell", 60, 0), ("move", 40, 2 * H), ("dwell", 300, 0)]
    )
    track = estimate_floors(times, alts, H)
    assert [(c.start, c.end) for c in track.changes] == [(0, 2)]
    assert sorted(set(track.floors)) == [0, 2] and track.floors[-1] == 2


def test_weather_drift_over_a_long_visit_does_not_climb_floors() -> None:
    # 2 hPa over six hours (about 17 m of apparent climb) while sitting on floor 1.
    drift = 2 * 8.43 / (6 * 3600)
    plan = [("dwell", 60, 0), ("move", 20, H), ("dwell", 6 * 3600, 0)]
    times, alts, _ = trace(plan, drift_mps=drift)
    track = estimate_floors(times, alts, H)
    assert track.floors[-1] == 1 and len(track.changes) == 1
    assert naive_floors(alts, H)[-1] >= 5  # the entry-referenced estimate climbs five phantom floors


def test_elevator_up_and_stairs_down() -> None:
    plan = [("dwell", 90, 0), ("move", 12, 3 * H), ("dwell", 240, 0), ("move", 50, -2 * H), ("dwell", 120, 0)]
    times, alts, truth = trace(plan)
    track = estimate_floors(times, alts, H)
    assert [(c.start, c.end) for c in track.changes] == [(0, 3), (3, 1)]
    # Away from each movement (the median filter lags by half its window), floors match the truth.
    settled = [i for i, t in enumerate(times) if all(not c.t - 15 <= t <= c.t_end + 15 for c in track.changes)]
    assert all(track.floors[i] == truth[i] for i in settled)


@pytest.mark.parametrize("seed", range(5))
def test_sensor_noise_alone_never_changes_floor(seed: int) -> None:
    times, alts, _ = trace([("dwell", 3600, 0)], noise=0.25, seed=seed)
    assert estimate_floors(times, alts, H).changes == ()


def test_a_half_level_mezzanine_stays_put_until_the_full_floor() -> None:
    times, alts, _ = trace(
        [("dwell", 60, 0), ("move", 10, 1.5), ("dwell", 120, 0), ("move", 10, 1.8), ("dwell", 120, 0)]
    )
    track = estimate_floors(times, alts, H)
    assert [(c.start, c.end) for c in track.changes] == [(0, 1)]


def test_health_flights_raise_confidence_and_unknown_heights_cap_it() -> None:
    times, alts, _ = trace([("dwell", 60, 0), ("move", 40, H), ("dwell", 120, 0)])
    plain = estimate_floors(times, alts, H).changes[0].confidence
    assert estimate_floors(times, alts, H, flights=[100.0]).changes[0].confidence > plain
    assert estimate_floors(times, alts, H, height_known=False).changes[0].confidence <= 0.75


def test_pressure_converts_to_metres_and_heights_resolve_in_order() -> None:
    (zero, one_hpa) = altitude_from_pressure([1013.25, 1012.25])
    assert zero == 0 and 8.2 < one_hpa < 8.6
    assert floor_height({"floor_height_m": 3.2}) == (3.2, True)
    assert floor_height(None, {"height": "20 m", "building:levels": "4"}) == (5.0, True)
    assert floor_height() == (3.3, False)


def test_floor_events_match_the_event_contract() -> None:
    times, alts, _ = trace([("dwell", 60, 0), ("move", 30, 2 * H), ("dwell", 60, 0)])
    start = datetime(2026, 10, 20, 9, 20, tzinfo=UTC)
    events = floor_events(
        estimate_floors(times, alts, H),
        place="hall",
        iso=lambda s: (start + timedelta(seconds=s)).isoformat(),
        source_ref="skeleton:hall",
    )
    assert [e["label"] for e in events] == ["Up to floor 2"]
    for event in events:
        validate("event", event)

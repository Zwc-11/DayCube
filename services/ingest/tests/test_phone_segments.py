"""Indexed sample windows preserve closed endpoints, original order and segmentation semantics."""

import math
import random
from datetime import UTC, datetime, timedelta

from ingest.phone.sensor_logger import Recording
from ingest.phone.visits import _Samples, segment
from ingest.skeleton.parsers.types import Fix


def test_sample_windows_match_original_scans_with_duplicates_and_unordered_inputs() -> None:
    rng = random.Random(71)
    for ordered in (False, True):
        for _ in range(80):
            times = [float(rng.randrange(20)) for _ in range(60)]
            if ordered:
                times.sort()
            rows = list(range(len(times)))
            samples = _Samples(rows, iter(times))
            for start, end in ((0, 19), (3, 3), (4.5, 7.5), (-1, 30), (20, 21), (9, 2)):
                assert samples.window(start, end) == [
                    row for row, t in zip(rows, times, strict=True) if start <= t <= end
                ]
            assert samples.times.itemsize == 8


def test_empty_and_nonfinite_samples_and_query_bounds_keep_scan_behavior() -> None:
    for times in ([], [math.nan], [-math.inf, 0.0, math.inf], [0.0, math.nan, 1.0]):
        rows = list(range(len(times)))
        samples = _Samples(rows, times)
        for start, end in ((-math.inf, math.inf), (0, 0), (math.nan, 1), (0, math.nan)):
            assert samples.window(start, end) == [row for row, t in zip(rows, times, strict=True) if start <= t <= end]


def test_adjacent_spans_both_keep_fixes_exactly_on_the_shared_endpoint() -> None:
    rows = [object(), object(), object(), object()]
    samples = _Samples(rows, [0, 1, 1, 2])
    assert samples.window(0, 1) == rows[:3]
    assert samples.window(1, 2) == rows[1:]
    assert samples.window(1, 1) == rows[1:3]


def test_segmentation_keeps_unordered_source_fixes_and_step_order() -> None:
    start = datetime(2026, 1, 15, tzinfo=UTC)
    epoch = start.timestamp()
    fixes = [Fix(start + timedelta(seconds=t), 45, -73, source="synthetic") for t in (0, 200, 100, 1200)]
    steps = [(epoch + t, n) for t, n in ((0, 0), (100, 5), (50, 3), (1200, 10))]
    recording = Recording("UTC", "Synthetic", "fixture", fixes, steps=steps, activity=[(epoch, "stationary")])
    spans = segment(recording)
    assert len(spans) == 1
    span = spans[0]
    assert (span.kind, span.mode, span.t0, span.t1, span.steps) == ("stay", "venue", epoch, epoch + 1200, 10)
    assert span.fixes == fixes and all(a is b for a, b in zip(span.fixes, fixes, strict=True))
    assert span.conf == 0.56 and (span.lat, span.lon, span.distance_m) == (45, -73, 0)

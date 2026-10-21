import random
from datetime import timedelta

import pytest
from ingest.index.timeline import EventTimeline
from ingest.time import instant


def event(label: str, timestamp: str) -> dict:
    return {"id": label, "t": timestamp}


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-10-20T08:49:59.999999Z", None),
        ("2026-10-20T08:50:00Z", "first"),
        ("2026-10-20T09:00:00Z", "first"),
        ("2026-10-20T09:05:00Z", "first"),
        ("2026-10-20T09:05:00.000001Z", "later"),
        ("2026-10-20T05:10:00-04:00", "later"),
        ("2026-10-20T09:20:00Z", "later"),
        ("2026-10-20T09:20:00.000001Z", None),
    ],
)
def test_nearest_ties_duplicates_offsets_and_cutoff(timestamp: str, expected: str | None) -> None:
    timeline = EventTimeline(
        [
            event("later", "2026-10-20T09:10:00Z"),
            event("first", "2026-10-20T09:00:00Z"),
            event("duplicate", "2026-10-20T05:00:00-04:00"),
        ]
    )
    nearest = timeline.nearest(timestamp)
    assert (nearest["id"] if nearest else None) == expected


def test_no_events_means_no_inferred_location() -> None:
    assert EventTimeline([]).nearest("2026-10-20T09:00:00Z") is None


def test_single_event_at_and_outside_cutoff() -> None:
    record = event("only", "2026-10-20T09:00:00Z")
    timeline = EventTimeline([record])
    assert timeline.nearest("2026-10-20T08:50:00Z") is record
    assert timeline.nearest("2026-10-20T09:10:00Z") is record
    assert timeline.nearest("2026-10-20T09:10:01Z") is None


def test_lookup_matches_exhaustive_search_on_seeded_timeline() -> None:
    rng = random.Random(20261020)
    start = instant("2026-10-20T08:00:00Z")
    events = [
        event(str(index), (start + timedelta(seconds=rng.randrange(12 * 3600))).isoformat()) for index in range(250)
    ]
    events = sorted(events, key=lambda record: instant(record["t"]))
    timeline = EventTimeline(events)
    for _ in range(100):
        target = start + timedelta(seconds=rng.randrange(-3600, 13 * 3600))
        nearest = min(events, key=lambda record: abs(instant(record["t"]) - target))
        expected = nearest if abs(instant(nearest["t"]) - target) <= timedelta(minutes=10) else None
        assert timeline.nearest(target.isoformat()) is expected

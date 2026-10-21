from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..time import instant

if TYPE_CHECKING:
    from ..vault import EventSeries


class EventTimeline:
    """Nearest evidence within ten minutes, with stable ties across duplicate timestamps."""

    def __init__(self, events: Iterable[dict[str, Any]], starts: Iterable[datetime] | None = None) -> None:
        # Keep the first record at a given instant, matching the stable event ordering.
        by_time: dict[datetime, dict[str, Any]] = {}
        if starts is None:
            for event in events:
                by_time.setdefault(instant(event["t"]), event)
        else:
            for event, start in zip(events, starts, strict=True):
                by_time.setdefault(start, event)
        self.times = sorted(by_time)
        self.events = [by_time[timestamp] for timestamp in self.times]

    @classmethod
    def from_series(cls, series: EventSeries) -> EventTimeline:
        """Reuse instants the series already parsed; its records are sorted by start."""
        return cls(series.records, series.starts)

    def nearest(self, timestamp: str) -> dict[str, Any] | None:
        target = instant(timestamp)
        insertion = bisect_left(self.times, target)
        # Only the two neighbors can be nearest; visit the earlier one first for ties.
        neighbors = range(max(0, insertion - 1), min(len(self.times), insertion + 1))
        index = min(neighbors, key=lambda i: abs(self.times[i] - target), default=None)
        if index is None or abs(self.times[index] - target) > timedelta(minutes=10):
            return None
        return self.events[index]

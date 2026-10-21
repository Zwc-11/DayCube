from bisect import bisect_left
from datetime import datetime, timedelta

from ..time import instant


class EventTimeline:
    """Nearest evidence within ten minutes, with stable ties across duplicate timestamps."""

    def __init__(self, events: list[dict]) -> None:
        # Keep the first record at a given instant, matching the stable event ordering.
        by_time: dict[datetime, dict] = {}
        for event in events:
            by_time.setdefault(instant(event["t"]), event)
        self.times = sorted(by_time)
        self.events = [by_time[timestamp] for timestamp in self.times]

    def nearest(self, timestamp: str) -> dict | None:
        target = instant(timestamp)
        insertion = bisect_left(self.times, target)
        # Only the two neighbors can be nearest; visit the earlier one first for ties.
        neighbors = range(max(0, insertion - 1), min(len(self.times), insertion + 1))
        index = min(neighbors, key=lambda i: abs(self.times[i] - target), default=None)
        if index is None or abs(self.times[index] - target) > timedelta(minutes=10):
            return None
        return self.events[index]

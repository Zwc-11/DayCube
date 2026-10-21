from datetime import datetime
from zoneinfo import ZoneInfo


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Timestamps must include a UTC offset")
    return parsed


def tod(value: str, timezone: str) -> float:
    """Wall-clock seconds since local midnight; never decimal hours."""
    local = instant(value).astimezone(ZoneInfo(timezone))
    return local.hour * 3600 + local.minute * 60 + local.second + local.microsecond / 1e6


def overlaps(start: str, end: str | None, t0: str, t1: str) -> bool:
    left, right = instant(start), instant(end or start)
    lower, upper = instant(t0), instant(t1)
    return left < upper and (right > lower if right > left else left >= lower)

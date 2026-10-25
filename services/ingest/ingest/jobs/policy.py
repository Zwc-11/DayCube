"""When the GPU may be taken: the person has been away from the PC long enough, or it is night."""

from __future__ import annotations

from datetime import datetime

IDLE_S = 10 * 60
NIGHT = (1, 7)  # local hours [from, to)


def free(idle_s: float | None, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return (idle_s is not None and idle_s >= IDLE_S) or NIGHT[0] <= now.hour < NIGHT[1]

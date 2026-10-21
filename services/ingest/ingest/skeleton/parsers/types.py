from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Fix:
    t: datetime
    lat: float
    lon: float
    hacc: float | None = None
    alt_abs: float | None = None
    alt_rel: float | None = None
    steps: int | None = None
    heading: float | None = None
    source: str = "unknown"

    def __post_init__(self):
        if self.t.utcoffset() is None:
            raise ValueError("Fix timestamps must have an offset")
        if not -90 <= self.lat <= 90 or not -180 <= self.lon <= 180:
            raise ValueError("Invalid geographic coordinates")

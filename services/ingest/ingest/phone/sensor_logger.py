"""A Sensor Logger recording (iOS/Android app "Sensor Logger", CSV export) as fixes and samples.

The export is a zip (or its unpacked folder) of one CSV per sensor, every row stamped ``time`` in
epoch nanoseconds, and ``Metadata.csv`` with the recording's time zone. Read here:

- ``Location.csv``: latitude, longitude, horizontal accuracy (m), altitude, speed (m/s; -1: none),
  bearing (degrees; -1: none). Rows repeated by the app (same place, same accuracy) are kept once;
  fixes worse than ``MAX_ACCURACY_M`` are dropped.
- ``Barometer.csv``: relative altitude (m) and pressure (hPa), about 1 Hz, for floors.
- ``Pedometer.csv``: steps since the recording began.
- ``Activity.csv``: the phone's motion activity (stationary, walking, running, cycling,
  automotive, unknown), for how each stretch was travelled.

Magnetometer and other high-rate sensors are not read (tens of MB, nothing a day needs).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..skeleton.parsers.types import Fix

MAX_ACCURACY_M = 100.0
SOURCE = "sensor-logger"


@dataclass
class Recording:
    tz: str
    name: str  # the recording's name without its date (the app names it after where it began)
    device: str
    fixes: list[Fix]
    altitude: list[tuple[float, float]] = field(default_factory=list)  # (epoch s, relative altitude m)
    steps: list[tuple[float, int]] = field(default_factory=list)  # (epoch s, steps so far)
    activity: list[tuple[float, str]] = field(default_factory=list)  # (epoch s, label)

    @property
    def t0(self) -> float:
        return min(t for t in self._times() if t is not None)

    @property
    def t1(self) -> float:
        return max(t for t in self._times() if t is not None)

    def _times(self) -> Iterator[float | None]:
        for series in (self.altitude, self.steps, self.activity):
            if series:
                yield series[0][0]
                yield series[-1][0]
        if self.fixes:
            yield self.fixes[0].t.timestamp()
            yield self.fixes[-1].t.timestamp()

    def altitude_at(self, t: float) -> float | None:
        """Barometric relative altitude at ``t``, linearly between samples (None outside them)."""
        return _interpolate(self.altitude, t)


def _interpolate(series: list[tuple[float, float]], t: float) -> float | None:
    if not series or t < series[0][0] or t > series[-1][0]:
        return None
    i = bisect_left(series, (t, -float("inf")))
    if i == 0:
        return series[0][1]
    (ta, va), (tb, vb) = series[i - 1], series[i]
    return va if tb == ta else va + (vb - va) * (t - ta) / (tb - ta)


class _Files:
    """The CSVs of a zip or a folder, by name."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.zip = zipfile.ZipFile(path) if path.is_file() else None
        names = self.zip.namelist() if self.zip else [p.name for p in path.iterdir()]
        self.names = {Path(n).name: n for n in names if n.lower().endswith(".csv")}

    def rows(self, name: str) -> list[dict[str, str]]:
        member = self.names.get(name)
        if member is None:
            return []
        if self.zip:
            with self.zip.open(member) as raw:
                return list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")))
        with (self.path / member).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def close(self) -> None:
        if self.zip:
            self.zip.close()


def is_recording(path: Path) -> bool:
    """Whether ``path`` (a zip or folder) is a Sensor Logger export."""
    try:
        files = _Files(path)
    except (OSError, zipfile.BadZipFile):
        return False
    try:
        return "Metadata.csv" in files.names and "Location.csv" in files.names
    finally:
        files.close()


def parse(path: Path) -> Recording:
    files = _Files(path)
    try:
        meta = (files.rows("Metadata.csv") or [{}])[0]
        tz = (meta.get("recording timezone") or "UTC").strip()
        ZoneInfo(tz)  # an unknown zone fails here, not later
        zone = ZoneInfo(tz)
        name = re.sub(r"[-_]?\d{4}-\d{2}-\d{2}[_ ]\d{2}-\d{2}-\d{2}$", "", path.stem).replace("_", " ").strip()
        if re.search(r"(?:^|[-_])sensors-\d{8}T\d{6}Z$", path.name):
            name = (meta.get("recording name") or "").strip()  # DayCube Capture names its hours by their start
        if path.is_dir() and (path / "name.txt").is_file():  # a copy kept in vault/sources (phone/sources.py)
            name = (path / "name.txt").read_text(encoding="utf-8").strip()
        altitude = sorted(
            (_seconds(r["time"]), float(r["relativeAltitude"]))
            for r in files.rows("Barometer.csv")
            if _number(r.get("relativeAltitude")) is not None
        )
        steps = sorted(
            (_seconds(r["time"]), int(float(r["steps"]))) for r in files.rows("Pedometer.csv") if r.get("steps")
        )
        activity = sorted((_seconds(r["time"]), r.get("activity", "unknown").strip() or "unknown")
                          for r in files.rows("Activity.csv"))  # fmt: skip
        recording = Recording(tz, name, (meta.get("device name") or "").strip(), [], altitude, steps, activity)
        recording.fixes = _fixes(files.rows("Location.csv"), zone, recording)
        return recording
    finally:
        files.close()


def _fixes(rows: list[dict[str, str]], zone: ZoneInfo, recording: Recording) -> list[Fix]:
    fixes: list[Fix] = []
    last: tuple | None = None
    step_series = [(t, float(s)) for t, s in recording.steps]
    for row in sorted(rows, key=lambda r: int(r["time"])):
        lat, lon, accuracy = (
            _number(row.get("latitude")),
            _number(row.get("longitude")),
            _number(row.get("horizontalAccuracy")),
        )
        if lat is None or lon is None or (accuracy is not None and (accuracy < 0 or accuracy > MAX_ACCURACY_M)):
            continue
        key = (lat, lon, accuracy)
        if key == last:
            continue  # the app repeats the last fix until a new one comes
        last = key
        t = _seconds(row["time"])
        bearing = _number(row.get("bearing"))
        steps = _interpolate(step_series, t)
        fixes.append(Fix(
            t=datetime.fromtimestamp(t, UTC).astimezone(zone), lat=lat, lon=lon, hacc=accuracy,
            alt_abs=_number(row.get("altitudeAboveMeanSeaLevel")) or _number(row.get("altitude")),
            alt_rel=recording.altitude_at(t), steps=None if steps is None else int(steps),
            heading=bearing if bearing is not None and bearing >= 0 else None, source=SOURCE,
        ))  # fmt: skip
    return fixes


def speeds(rows: list[Fix]) -> list[float]:
    """Ground speed (m/s) at each fix, from its neighbours."""
    from .geo import metres

    out = []
    for i in range(len(rows)):
        a, b = rows[max(i - 1, 0)], rows[min(i + 1, len(rows) - 1)]
        dt = (b.t - a.t).total_seconds()
        out.append(metres(a.lat, a.lon, b.lat, b.lon) / dt if dt > 0 else 0.0)
    return out


def _seconds(nanoseconds: str) -> float:
    return int(nanoseconds) / 1e9


def _number(value: str | None) -> float | None:
    try:
        number = float(value) if value not in (None, "") else None
    except ValueError:
        return None
    return number if number is not None and number == number else None


def join(recordings: list[Recording], *, max_gap_s: float = 600.0) -> list[Recording]:
    """Recordings that follow one another (the same phone, the same time zone, less than
    ``max_gap_s`` apart: DayCube Capture's hours, or Sensor Logger restarted) as one, so a stay
    across the hour is one visit. Relative altitude and steps start again at 0 in each recording:
    they are carried on from where the previous one ended, as nothing is measured in the gap."""
    out: list[Recording] = []
    for recording in sorted((r for r in recordings if r.fixes or r.altitude), key=lambda r: r.t0):
        last = out[-1] if out else None
        if last is None or last.device != recording.device or last.tz != recording.tz or recording.t0 - last.t1 > max_gap_s:
            out.append(Recording(recording.tz, recording.name, recording.device, list(recording.fixes),
                                 list(recording.altitude), list(recording.steps), list(recording.activity)))  # fmt: skip
            continue
        lift = (last.altitude[-1][1] - recording.altitude[0][1]) if last.altitude and recording.altitude else 0.0
        steps = (last.steps[-1][1] - recording.steps[0][1]) if last.steps and recording.steps else 0
        seen = {f.t for f in last.fixes}
        last.fixes += [f for f in recording.fixes if f.t not in seen]
        last.altitude += [(t, a + lift) for t, a in recording.altitude]
        last.steps += [(t, n + steps) for t, n in recording.steps]
        last.activity += recording.activity
        last.name = last.name or recording.name
    return out

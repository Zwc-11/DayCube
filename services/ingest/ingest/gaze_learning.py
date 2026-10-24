"""The eye tracker's learning, for the eye-tracking dashboard: is the gaze getting better?

The gaze tracker (services/gaze, ``gaze.learn``) learns from clicks and writes three files next
to its calibration in ``vault/calibration``:

- ``gaze.json``: the calibration (when, which model, its held-out error);
- ``clicks.jsonl``: every click, with the error of the model in use at that moment, the error the
  calibration alone would have made on it (``base_px``), the model version in use (``v``) and
  whether it passed the "was looking" gate (``used``). Each click is measured before anything
  learns from it, so both errors are on data neither model had seen: their ratio is the live
  answer to "does learning help";
- ``learning.jsonl``: each fit (held-out errors of the calibration, the model in use and the
  candidate, whether it was taken) and the first frame each update reached (``applied``).

``report`` reads them for the current calibration only (a new calibration starts learning over)
and returns the clicks as columns (compact, and what charts take), the fits, the applications,
and the live numbers. Errors are given in pixels and, where the head's distance is known (it is
in each click's frame vector), in degrees of visual angle, the unit eye trackers are compared in.

The click log grows by a line per click and is read incrementally: only lines appended since the
last read are parsed (the file is reparsed when it is rewritten, which the tracker does when it
trims it). ``tagged`` serialises the report once per change of the four files and tags it, so a
dashboard polling every few seconds gets an empty 304 until a click or a fit lands.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .vault import Record, file_stamp

CALIBRATION_FILE = "gaze.json"
CLICKS_FILE = "clicks.jsonl"
HISTORY_FILE = "learning.jsonl"
LEARNED_FILE = "gaze-learned.json"
HEAD_T = slice(9, 12)  # the head's position (mm, camera coordinates) in a click's frame vector
LIVE_CLICKS = 300  # the live numbers are over the newest clicks
MAX_CLICKS = 5000  # sent to the dashboard: the newest


@dataclass
class _Tail:
    """One JSON-lines file read incrementally: parsed rows and how far the file was read."""

    key: tuple[int, int] | None = None  # (device, inode) of the file read
    offset: int = 0
    rows: list[Record] = field(default_factory=list)
    partial: bytes = b""


class _Tails:
    def __init__(self) -> None:
        self._tails: dict[Path, _Tail] = {}
        self._lock = threading.Lock()

    def read(self, path: Path, shape: Any) -> list[Record]:
        """Every row of ``path`` (each a JSON object passed through ``shape``; None drops it),
        parsing only what was appended since the last call."""
        with self._lock:
            tail = self._tails.setdefault(path, _Tail())
            try:
                stat = path.stat()
            except OSError:
                self._tails[path] = _Tail()
                return []
            key = (stat.st_dev, stat.st_ino)
            if key != tail.key or stat.st_size < tail.offset:  # replaced or rewritten: start over
                tail = self._tails[path] = _Tail(key=key)
            if stat.st_size > tail.offset:
                with path.open("rb") as stream:
                    stream.seek(tail.offset)
                    data = tail.partial + stream.read(stat.st_size - tail.offset)
                tail.offset = stat.st_size
                lines = data.split(b"\n")
                tail.partial = lines.pop()  # an append in flight: parsed once it is complete
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        row = shape(json.loads(line))
                    except (ValueError, TypeError, KeyError):
                        continue
                    if row is not None:
                        tail.rows.append(row)
            return tail.rows


TAILS = _Tails()


def _click(data: dict) -> Record:
    X = data.get("X") or []
    distance = math.hypot(*X[HEAD_T]) if len(X) >= HEAD_T.stop else math.nan
    return {
        "t": float(data["t"]),
        "error_px": _number(data.get("error_px")),
        "base_px": _number(data.get("base_px")),
        "version": int(data.get("v") or 0),
        "used": data.get("used"),
        "distance_mm": distance if math.isfinite(distance) and distance > 0 else math.nan,
    }


def _event(data: dict) -> Record | None:
    return data if isinstance(data, dict) and data.get("event") in ("fit", "applied") else None


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def _epoch(iso: Any) -> float | None:
    if not isinstance(iso, str):
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def _rounded(values: np.ndarray, digits: int) -> list[float | None]:
    return [None if not math.isfinite(v) else round(v, digits) for v in values.tolist()]


def _median(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    return round(float(np.median(finite)), 2) if len(finite) else None


def report(root: Path) -> Record:
    """The learning of the current calibration in ``root`` (the vault)."""
    folder = root / "calibration"
    try:
        calibration = json.loads((folder / CALIBRATION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"calibrated": False}
    since = _epoch(calibration.get("t"))
    mm_per_px = _number(calibration.get("mm_per_px"))
    rows = [r for r in TAILS.read(folder / CLICKS_FILE, _click) if since is None or r["t"] >= since]
    rows = rows[-MAX_CLICKS:]
    events = [
        e for e in TAILS.read(folder / HISTORY_FILE, _event) if since is None or (_epoch(e.get("t")) or 0) >= since
    ]
    learned = None
    try:
        data = json.loads((folder / LEARNED_FILE).read_text(encoding="utf-8"))
        if data.get("calibration") == calibration.get("t"):
            learned = {"version": data.get("version"), "t": data.get("t"), "fit": data.get("fit"),
                       "head_terms": data.get("pose_ref") is not None}  # fmt: skip
    except (OSError, ValueError, AttributeError):
        pass

    t = np.array([r["t"] for r in rows], dtype=float)
    error = np.array([r["error_px"] for r in rows], dtype=float)
    base = np.array([r["base_px"] for r in rows], dtype=float)
    distance = np.array([r["distance_mm"] for r in rows], dtype=float)
    # Degrees of visual angle: the error on screen seen from where the head was.
    with np.errstate(invalid="ignore", divide="ignore"):
        degrees = np.degrees(np.arctan2(error * mm_per_px, distance)) if math.isfinite(mm_per_px) else error * np.nan
        base_degrees = np.degrees(np.arctan2(base * mm_per_px, distance)) if math.isfinite(mm_per_px) else base * np.nan
    recent = slice(max(0, len(rows) - LIVE_CLICKS), None)
    both = np.isfinite(error[recent]) & np.isfinite(base[recent])
    model_px, base_px = _median(error[recent][both]), _median(base[recent][both])
    fits = [e for e in events if e["event"] == "fit"]
    applied = [e for e in events if e["event"] == "applied"]
    return {
        "calibrated": True,
        "calibration": {
            "t": calibration.get("t"),
            "model": calibration.get("model"),
            "monitor": calibration.get("monitor"),
            "error_px": calibration.get("error_px"),
            "error_p90_px": calibration.get("error_p90_px"),
            "moved_error_px": calibration.get("moved_error_px"),
            "mm_per_px": mm_per_px if math.isfinite(mm_per_px) else None,
        },  # fmt: skip
        "learned": learned,
        "live": {
            "clicks": int(both.sum()),
            "error_px": model_px,
            "calibration_px": base_px,
            "ratio": round(model_px / base_px, 3) if model_px is not None and base_px else None,
            "error_deg": _median(degrees[recent][both]),
            "calibration_deg": _median(base_degrees[recent][both]),
            "distance_mm": _median(distance[recent]),
        },
        "clicks": {
            "t": _rounded(t, 3),
            "error_px": _rounded(error, 1),
            "base_px": _rounded(base, 1),
            "version": [r["version"] for r in rows],
            "used": [r["used"] for r in rows],
            "distance_mm": _rounded(distance, 0),
        },
        "fits": fits[-200:],
        "applied": applied[-200:],
        "files": {name: (folder / name).is_file() for name in (CLICKS_FILE, HISTORY_FILE, LEARNED_FILE)},
    }


_TAGGED: dict[Path, tuple[tuple, bytes, str]] = {}
_TAGGED_LOCK = threading.Lock()


def tagged(root: Path) -> tuple[bytes, str]:
    """The report as JSON bytes and its ETag, rebuilt only when one of its files changed."""
    folder = root / "calibration"
    stamps = tuple(file_stamp(folder / name) for name in (CALIBRATION_FILE, CLICKS_FILE, HISTORY_FILE, LEARNED_FILE))
    with _TAGGED_LOCK:
        cached = _TAGGED.get(root)
        if cached is not None and cached[0] == stamps:
            return cached[1], cached[2]
    body = json.dumps(report(root), separators=(",", ":"), allow_nan=False).encode("utf-8")
    etag = f'"{hashlib.blake2b(body, digest_size=12).hexdigest()}"'
    with _TAGGED_LOCK:
        _TAGGED[root] = (stamps, body, etag)
    return body, etag

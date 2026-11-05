"""The capture logs a review reads beside the timeline (phase 3 of the daily summary plan).

- ``inputs.jsonl``: typing and mouse counts per 10 s (``capture-win/capture/inputs.py``), counts
  only. Desk time with input is told apart from reading or watching with none.
- ``doing.jsonl``: lines saying what you were doing (the Alt+Shift+N box, ``POST /doing``).
- ``phone-events.jsonl``: apps opened and closed on the phone (iOS Shortcuts, ``POST /phone/events``),
  made into sessions: an app's use runs from its opening to its closing; an opening with no closing
  ends at the next event or after ``SESSION_MAX_S``. A pickup is a session that starts at least
  ``PICKUP_GAP_S`` after the last one ended.

Each reader takes the calendar days a review day spans and returns what falls in its window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..time import instant
from ..vault import Record, Vault, stored, text_lines

SESSION_MAX_S = 30 * 60.0  # an app opened with no closing after it counts at most this long
PICKUP_GAP_S = 60.0  # a session starting this long after the last one ended is a new pickup
INPUTS, DOING, PHONE = "inputs.jsonl", "doing.jsonl", "phone-events.jsonl"


def _lines(vault: Vault, days: list[str], name: str) -> list[Record]:
    import json

    out = []
    for day in days:
        path = vault.root / "capture" / day / name
        if not stored(path).exists():
            continue
        for line in text_lines(path):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue  # a torn append
            if isinstance(value, dict) and isinstance(value.get("t"), str):
                out.append(value)
    return out


def _at(record: Record) -> float | None:
    try:
        return instant(record["t"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class Bucket:
    t0: float
    t1: float
    keys: int
    clicks: int
    wheel: int
    move_px: int


def inputs(vault: Vault, days: list[str], start: datetime, stop: datetime) -> list[Bucket]:
    """Buckets of input counts inside the window, in time order."""
    a, b = start.timestamp(), stop.timestamp()
    out = []
    for record in _lines(vault, days, INPUTS):
        t = _at(record)
        if t is None or not a <= t < b:
            continue
        try:
            span = float(record.get("s", 10))
            counts = [max(0, int(record.get(k, 0) or 0)) for k in ("keys", "clicks", "wheel", "move_px")]
        except (TypeError, ValueError):
            continue
        if any(counts):
            out.append(Bucket(t, min(t + span, b), *counts))
    out.sort(key=lambda bucket: bucket.t0)
    return out


def doing(vault: Vault, days: list[str], start: datetime, stop: datetime) -> list[Record]:
    """Doing-now lines inside the window, each with its id, in time order."""
    a, b = start.timestamp(), stop.timestamp()
    out = []
    for n, record in enumerate(_lines(vault, days, DOING)):
        t = _at(record)
        text = " ".join(str(record.get("text") or "").split())
        if t is None or not a <= t < b or not text:
            continue
        out.append({"t": record["t"], "ts": t, "id": str(record.get("id") or f"{record['t']}-{n}"), "text": text})
    out.sort(key=lambda r: r["ts"])
    return out


@dataclass(frozen=True, slots=True)
class Session:
    app: str
    t0: float
    t1: float
    ids: tuple[str, ...]  # the events it was made from
    closed: bool  # ended by its own "closed" event


def phone(vault: Vault, days: list[str], start: datetime, stop: datetime) -> list[Session]:
    """App sessions inside the window (clipped to it), in time order."""
    events = []
    for record in _lines(vault, days, PHONE):
        t = _at(record)
        app = str(record.get("app") or "").strip()
        if t is None or not app or record.get("event") not in ("opened", "closed"):
            continue
        events.append((t, record["event"], app, str(record.get("id") or "")))
    events.sort(key=lambda e: (e[0], e[1] == "opened"))  # a closing at the same moment first
    sessions: list[Session] = []
    open_: dict[str, tuple[float, str]] = {}  # app (case-folded) -> (opened at, event id)
    names: dict[str, str] = {}

    def end(key: str, t: float, ids: tuple[str, ...], closed: bool) -> None:
        t0, first = open_.pop(key)
        sessions.append(Session(names[key], t0, min(t, t0 + SESSION_MAX_S), (first, *ids), closed))

    for t, kind, app, event_id in events:
        key = app.casefold()
        names.setdefault(key, app)
        if kind == "opened":
            for other in [k for k in open_ if k != key]:  # its closing was missed: it ended here
                end(other, t, (), False)
            if key not in open_:
                open_[key] = (t, event_id)
        elif key in open_:
            end(key, t, (event_id,), True)
    for key in list(open_):
        t0, _ = open_[key]
        end(key, t0 + SESSION_MAX_S, (), False)
    a, b = start.timestamp(), stop.timestamp()
    clipped = []
    for s in sessions:
        t0, t1 = max(s.t0, a), min(s.t1, b)
        if t1 > t0:
            clipped.append(Session(s.app, t0, t1, tuple(i for i in s.ids if i), s.closed))
    clipped.sort(key=lambda s: s.t0)
    return clipped


def pickups(sessions: list[Session]) -> list[Session]:
    """The sessions that begin a pickup: at least ``PICKUP_GAP_S`` after the previous one ended."""
    out = []
    ended = -1e18
    for s in sessions:
        if s.t0 - ended >= PICKUP_GAP_S:
            out.append(s)
        ended = max(ended, s.t1)
    return out


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))

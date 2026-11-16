"""How the day felt (docs/feeling.md): what the person reports, kept beside what was recorded.

No sensor measures a mood, so the report is the fact: energy and mood from 1 to 5, with an
optional note, a few times a day (``capture/<date>/feeling.jsonl``, a capture log like the
others, so pausing and deleting a span apply to it as to them).

Each report is shown with its evidence, never as a verdict: how fast the person was typing,
clicking and moving the mouse in the ten minutes before, how often the window in front changed,
and what they were doing (the day's timeline).

A personal model would estimate energy and mood from that evidence where nothing was reported.
It is held to a stop rule first (``judge``): once there are ``ENOUGH`` reports it must predict
the reports of left-out days better than the person's own average for that part of the day and
that activity, or it is dropped and only reports are ever shown.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from .review import timeline
from .review.signals import inputs
from .vault import Record, Vault, read_lines, stored

FILE = "feeling.jsonl"
SCALE = (1, 5)
BEFORE_S = 600.0  # the stretch before a report its evidence is taken from
ENOUGH = 100  # reports before the model is judged
MARGIN = 0.95  # the model's error must be under this share of the baseline's
RIDGE = 3.0
TARGETS = ("energy", "mood")
PARTS = ((5, "morning"), (12, "afternoon"), (18, "evening"), (23, "night"))


def _folder(vault: Vault, day: str) -> Path:
    return vault.root / "capture" / day


def report(vault: Vault, t: datetime, energy: int, mood: int, note: str | None = None) -> Record:
    """Keep a report made at ``t`` (with its UTC offset); returns it."""
    if not all(SCALE[0] <= v <= SCALE[1] for v in (energy, mood)):
        raise ValueError("Energy and mood are whole numbers from 1 to 5")
    if t.tzinfo is None:
        raise ValueError("A report's time needs its UTC offset")
    stamp = t.isoformat(timespec="seconds")
    record = {"id": f"fe-{t:%Y%m%d%H%M%S}", "t": stamp, "kind": "feeling", "energy": energy, "mood": mood}
    if note and note.strip():
        record["note"] = " ".join(note.split())[:280]
    folder = _folder(vault, t.date().isoformat())
    folder.mkdir(parents=True, exist_ok=True)
    vault.append_lines(folder / FILE, [record])
    return record


def reports(vault: Vault, day: str) -> list[Record]:
    """The reports made on calendar day ``day``, in time order."""
    path = _folder(vault, day) / FILE
    return sorted(read_lines(path), key=lambda r: r["t"]) if stored(path).exists() else []


def part_of_day(t: datetime) -> str:
    return next((name for hour, name in reversed(PARTS) if t.hour >= hour), "night")


def evidence(vault: Vault, t: datetime, zone: str) -> Record:
    """What was recorded in the ten minutes before ``t``: input per minute, window switches per
    minute, and what the timeline says was in front."""
    tz = ZoneInfo(zone)
    local = t.astimezone(tz)
    since = local - timedelta(seconds=BEFORE_S)
    buckets = inputs(vault, sorted({since.date().isoformat(), local.date().isoformat()}), since, local)
    minutes = BEFORE_S / 60
    out: Record = {
        f"{name}_per_min": round(sum(getattr(b, name) for b in buckets) / minutes, 1)
        for name in ("keys", "clicks", "move_px")
    }
    segments = timeline.read(vault, timeline.review_day(local, tz)) or []
    recent = [s for s in segments if s["until"] > since.isoformat() and s["t"] <= local.isoformat()]
    now = recent[-1] if recent and recent[-1]["until"] > local.isoformat() else None
    out["switches_per_min"] = round(sum(s.get("switches") or 0 for s in recent) / minutes, 2)
    out["doing"] = ((now.get("category") or now["where"]) if now else None) or "nothing recorded"
    out["part"] = part_of_day(local)
    return out


def _x(e: Record) -> list[float]:
    return [math.log1p(e["keys_per_min"]), math.log1p(e["clicks_per_min"]), math.log1p(e["move_px_per_min"]) / 5,
            e["switches_per_min"]]  # fmt: skip


def _baseline(train: list[Record], target: str) -> dict[tuple, float]:
    """The person's average by (part of day, activity), by part of day, and overall."""
    groups: dict[tuple, list[float]] = {}
    for r in train:
        for key in ((r["part"], r["doing"]), (r["part"],), ()):
            groups.setdefault(key, []).append(r[target])
    return {key: float(np.mean(values)) for key, values in groups.items()}


def _usual(baseline: dict[tuple, float], r: Record) -> float:
    return next(baseline[k] for k in ((r["part"], r["doing"]), (r["part"],), ()) if k in baseline)


def _fit(train: list[Record], target: str) -> tuple[dict[tuple, float], np.ndarray]:
    """The baseline and a ridge from the evidence to what is left of the report after it."""
    baseline = _baseline(train, target)
    x = np.array([[1.0, *_x(r)] for r in train])
    left = np.array([r[target] - _usual(baseline, r) for r in train])
    penalty = RIDGE * np.eye(x.shape[1])
    penalty[0, 0] = 0.0
    return baseline, np.linalg.solve(x.T @ x + penalty, x.T @ left)


def judge(rows: list[Record]) -> Record:
    """The stop rule on reports that carry their evidence (``part``, ``doing``, the per-minute
    numbers, ``day``): each day is left out in turn and predicted from the others."""
    out: Record = {"reports": len(rows), "enough": ENOUGH, "verdict": "collecting", "targets": {}}
    days = sorted({r["day"] for r in rows})
    if len(rows) < ENOUGH or len(days) < 2:
        return out
    kept = False
    for target in TARGETS:
        usual, model = [], []
        for day in days:
            train, test = [r for r in rows if r["day"] != day], [r for r in rows if r["day"] == day]
            baseline, weights = _fit(train, target)
            for r in test:
                base = _usual(baseline, r)
                usual.append(abs(r[target] - base))
                model.append(abs(r[target] - min(max(base + float(np.array([1.0, *_x(r)]) @ weights), 1.0), 5.0)))
        wins = float(np.mean(model)) < MARGIN * float(np.mean(usual))
        kept = kept or wins
        out["targets"][target] = {"usual_error": round(float(np.mean(usual)), 3),
                                  "model_error": round(float(np.mean(model)), 3), "kept": wins}  # fmt: skip
    out["verdict"] = "kept" if kept else "dropped"
    return out


def gathered(vault: Vault, zone: str) -> list[Record]:
    """Every report in the vault with its evidence, for the model and its judging."""
    rows = []
    for day in sorted({path.parent.name for path in (vault.root / "capture").glob(f"????-??-??/{FILE}*")}):
        for r in reports(vault, day):
            rows.append({**r, **evidence(vault, datetime.fromisoformat(r["t"]), zone), "day": day})
    return rows

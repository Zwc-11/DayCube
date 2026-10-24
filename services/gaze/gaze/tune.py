"""Tuning learning from clicks to one person, by replaying their own clicks.

The learner's settings (``gaze.learn.Settings``: how far off a click may be and still count, how
fast old labels fade, how hard the head terms and the eye's resting direction are held, how many
clicks the head terms wait for, how often to fit, how much a line being read counts) were chosen
on MPIIFaceGaze, 15 people on laptops. One desk, one webcam and one person's habits can want
others. ``vault/calibration/clicks.jsonl`` keeps every click's frame vector and pixel, and
``lines.jsonl`` every reading-line label with its line's extent, so learning can be run again from
the calibration on them, exactly as it ran live: each click predicted by the model of that moment
before anything learns from it (``replay``). That makes every setting comparable on the person's
own data.

Searching on the same clicks that judge the result would flatter it, so time is split. Settings
are searched (coordinate descent from the settings in use, two passes over the grid ``SEARCH``) on
the median error of the older 70% of clicks; the best is then compared with the settings in use on
the newest 30%, which the search never looked at. It is taken only if it is at least 3% better
there. Either way the outcome is logged in ``learning.jsonl``, and ``gaze-tuned.json`` holds the
settings in effect, which the tracker's learner reads again before its next fit.

    uv run --project services/gaze python -m gaze tune     # prints what it tried and what it chose

The tracker starts it on its own, at idle priority in a process of its own, at most once a day and
only after 200 new clicks (``due``).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .head import GazeModel
from .learn import (
    CLICKS_FILE,
    LINES_FILE,
    MAX_CLICKS,
    MAX_LINES,
    TUNED_FILE,
    Click,
    Learner,
    Settings,
    history,
    load_labels,
)

SEARCH: dict[str, tuple[float, ...]] = {
    "gate_k": (2.0, 3.0, 4.0),
    "half_life_days": (1.0, 3.0, 7.0, 30.0),
    "intercept_penalty": (0.03, 0.1, 0.3),
    "pose_penalty": (0.3, 1.0, 3.0),
    "pose_min_clicks": (40, 60, 120),
    "refit_every": (10, 20, 40),
    "line_weight": (0.0, 0.1, 0.25, 0.5),
}
MIN_CLICKS = 300  # clicks since calibration before tuning means anything
NEW_CLICKS = 200  # new clicks since the last tuning before another
EVERY_S = 20 * 3600.0
SEARCH_SHARE = 0.7  # the older share of clicks the search sees; the rest judges it
WARMUP = 50  # clicks after calibration whose errors do not count (every setting starts the same)
MARGIN = 0.97
PASSES = 2


class _Estimator:
    """What the learner needs of an estimator: the model and screen pixels from frame vectors."""

    def __init__(self, model: GazeModel) -> None:
        self.model = model

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        return self.model.predict_arrays(
            X[:, :9].reshape(-1, 3, 3), X[:, 12:18].reshape(-1, 2, 3), X[:, 18:].reshape(len(X), 2, -1)
        )


def replay(
    model_json: dict, monitor: tuple[int, int], clicks: list[Click], lines: list[Click], settings: Settings
) -> np.ndarray:
    """Learning run again from the calibration over ``clicks`` and ``lines`` in time order with
    ``settings``: each click's error (monitor px) against where it was clicked, measured by the
    model of that moment before anything learned from it."""
    estimator = _Estimator(GazeModel.from_json(model_json))
    learner = Learner(estimator, monitor, None, None, settings=settings)
    events = sorted([(c.t, 0, i) for i, c in enumerate(clicks)] + [(c.t, 1, i) for i, c in enumerate(lines)])
    errors = np.full(len(clicks), np.nan)
    for _, kind, i in events:
        if kind == 0:
            c = clicks[i]
            learner.add(c.t, c.x, c.y, c.X)
            errors[i] = learner.clicks[-1].error_px
        elif settings.line_weight > 0 and lines[i].span is not None:
            line = lines[i]
            learner.add_line(line.t, line.y, line.span[0], line.span[1], line.X)
    return errors


def _median(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(np.median(finite)) if len(finite) else math.inf


def search(
    run: Callable[[Settings], np.ndarray],
    start: Settings,
    judge: np.ndarray,
    grid: dict[str, tuple[float, ...]] = SEARCH,
    passes: int = PASSES,
) -> tuple[Settings, float, list[dict]]:
    """Coordinate descent over ``grid`` from ``start``, minimising the median error of the clicks
    ``judge`` selects; returns the best settings, their score and every trial."""
    scores: dict[Settings, float] = {}
    trials: list[dict] = []

    def score(s: Settings) -> float:
        if s not in scores:
            began = time.perf_counter()
            scores[s] = _median(run(s)[judge])
            trials.append({"settings": s.to_json(), "error_px": round(scores[s], 2),
                           "seconds": round(time.perf_counter() - began, 2)})  # fmt: skip
        return scores[s]

    best = start
    for _ in range(passes):
        moved = False
        for name, values in grid.items():
            for value in values:
                candidate = replace(best, **{name: type(getattr(best, name))(value)})
                if score(candidate) < score(best) - 1e-9:
                    best, moved = candidate, True
        if not moved:
            break
    return best, score(best), trials


def tune(vault: Path, *, say: Callable[[str], None] = print, grid: dict[str, tuple[float, ...]] | None = None) -> dict:
    """Replay this calibration's clicks to choose the learner's settings; writes ``gaze-tuned.json``
    and a ``tuned`` event, and returns what was done."""
    folder = vault / "calibration"
    started = time.perf_counter()
    try:
        calibration = json.loads((folder / "gaze.json").read_text(encoding="utf-8"))
        model_json = json.loads((folder / calibration["model_file"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError) as error:
        return {"tuned": False, "reason": f"no calibration: {error}"}
    if model_json.get("kind") != "head":
        return {"tuned": False, "reason": "the calibration is not the head-aware model, which is the one that learns"}
    monitor = (int(calibration["monitor"][0]), int(calibration["monitor"][1]))
    since = _epoch(calibration.get("t"))
    eyes = model_json["eyes"]
    shape = (18 + 2 * len(eyes[0]["mean"]),)
    clicks = load_labels(folder / CLICKS_FILE, since, shape, "click", MAX_CLICKS)
    lines = load_labels(folder / LINES_FILE, since, shape, "line", MAX_LINES)
    if len(clicks) < MIN_CLICKS:
        return {"tuned": False, "reason": f"{len(clicks)} clicks since calibrating; tuning needs {MIN_CLICKS}"}
    current = _current(folder)
    clicks.sort(key=lambda c: c.t)
    lines.sort(key=lambda c: c.t)
    cut = int(SEARCH_SHARE * len(clicks))
    index = np.arange(len(clicks))
    seen, held = (index >= WARMUP) & (index < cut), index >= cut
    grid = dict(grid or SEARCH)
    if not lines:
        grid.pop("line_weight", None)  # nothing to weigh
    say(f"replaying {len(clicks)} clicks and {len(lines)} reading lines since {calibration.get('t')}")

    def run(s: Settings) -> np.ndarray:
        return replay(model_json, monitor, clicks, lines, s)

    best, best_seen, trials = search(run, current, seen, grid)
    before, after = run(current), run(best)
    report = {
        "t": datetime.now(UTC).isoformat(), "clicks": len(clicks), "lines": len(lines),
        "calibration": calibration.get("t"),
        "replay": {
            "search_px": {"in_use": round(_median(before[seen]), 1), "best": round(best_seen, 1)},
            "held_out_px": {"in_use": round(_median(before[held]), 1), "best": round(_median(after[held]), 1)},
            "held_out_clicks": int(held.sum()), "trials": len(trials),
            "seconds": round(time.perf_counter() - started, 1),
        },
    }  # fmt: skip
    taken = best != current and _median(after[held]) <= MARGIN * _median(before[held])
    report["accepted"] = taken
    report["settings"] = (best if taken else current).to_json()
    report["changed"] = {
        k: [v, report["settings"][k]] for k, v in current.to_json().items() if report["settings"][k] != v
    }
    _write(folder / TUNED_FILE, report | {"trial_log": trials})
    history(folder, {"event": "tuned", **report})
    held_out = report["replay"]["held_out_px"]
    say(
        f"on the newest {int(held.sum())} clicks, which the search did not see: {held_out['in_use']} px with the "
        f"settings in use, {held_out['best']} px with the best found ({len(trials)} replays, "
        f"{report['replay']['seconds']} s)"
    )
    say(("taken: " + ", ".join(f"{k} {a} -> {b}" for k, (a, b) in report["changed"].items())) if taken
        else "kept the settings in use: the best found was not 3% better on the newest clicks")  # fmt: skip
    return {"tuned": True} | report


def due(folder: Path | None, clicks: list[Click], now: float | None = None) -> bool:
    """Whether a tuning should start: enough clicks, a day since the last tuning and enough clicks
    made since it."""
    if folder is None or len(clicks) < MIN_CLICKS:
        return False
    try:
        last = json.loads((folder / TUNED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    at = _epoch(last.get("t")) if isinstance(last, dict) else None
    if at is None:
        return True
    now = time.time() if now is None else now
    return now - at >= EVERY_S and sum(c.t > at for c in clicks) >= NEW_CLICKS


def start(vault: Path) -> Any:
    """``python -m gaze tune --background`` in a process of its own (no window, idle priority)."""
    import subprocess

    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    return subprocess.Popen(
        [sys.executable, "-m", "gaze", "tune", "--vault", str(vault), "--background"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
    )  # fmt: skip


def lower_priority() -> None:
    """Run at idle priority, so tuning never takes time from tracking or anything else."""
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00000040)  # IDLE_PRIORITY_CLASS
        else:
            os.nice(19)
    except (OSError, AttributeError):
        pass


def _current(folder: Path) -> Settings:
    try:
        data = json.loads((folder / TUNED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    return Settings.from_json(data.get("settings") if isinstance(data, dict) else None)


def _write(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    temporary.replace(path)


def _epoch(iso: Any) -> float | None:
    if not isinstance(iso, str):
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None

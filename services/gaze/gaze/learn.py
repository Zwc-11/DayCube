"""Learning from clicks: every click is a calibration point given without noticing.

People look at what they click. Each click pairs where the eyes were just before it (head pose and
eye measurements) with the point clicked, so the gaze model can keep learning after calibration, in
every app, as head positions, light and posture change through the day. Two published systems do
this: WebGazer (Papoutsaki et al., IJCAI 2016) adds clicks to its ridge regression, and PACE (Huang
et al., CHI 2016) reached 2.56 degrees from everyday clicks and typing alone. PACE's finding sets
how a click becomes a label here:

- **The look before the click.** Gaze lines up best with the click before it: 70 ms for large
  targets, 200-250 ms for small ones. Taking the gaze at the click itself gave labels 184 px off on
  average, against 74 px for a steady look before it. So the label is the eyes' last steady
  period of at least 80 ms ending within 0.5 s before the click (``steady``); a click with none is
  skipped.
- **Clicks made without looking** (a known button, the other monitor) are wrong labels. A click
  is used only if the model already put the gaze within 1/12 of the screen's diagonal of it. A
  click left out is checked again after each update, since a better model may accept it.

**Reading is labels too.** While the receiver's line decoder follows reading line by line, the
eyes are on the line being read: its height on the screen is known, and the eyes are somewhere
along it. Screen content as a weak label is how EVE (Park et al., ECCV 2020) went from 3.48 to
2.49 degrees. The receiver confirms a line only from the reading's own structure (a line read
between the one above and the one below, each reached by a return sweep) and sends it back with
the fixations on it (``line``): the label is the line's middle in height and, across it, where the
model put the gaze, held to the line's extent, so it teaches the vertical fully and the horizontal
only when the gaze fell off the line's ends. Lines count ``line_weight`` of a click in a fit, and
only clicks judge a fit: a decoder that went a line astray can move a candidate, never get it
taken unless clicks, which it cannot influence, say it is better.

How it learns: for each label the head pose gives the direction each eye must have had in the head
to look at the labelled point (``GazeModel.required``). The loss is the squared difference between
that direction and the model's, so it is the label's error on screen seen through the geometry. The
eye regression is refitted toward the labels, starting from the calibrated weights (a ridge
penalty pulls toward them, not toward zero), with newer labels weighted more (half-life a week)
and labels far off after a first fit weighted less (two Huber reweightings).

When it changes the model: every ``refit_every`` usable clicks (or 200 lines) it holds out the
newest clicks that the model in use has not learned from (at least 10, and at least 20% of all),
fits a candidate on the older clicks and the lines before them for each penalty strength, and
measures the median error in screen pixels on the held-out clicks, which neither the candidates
nor the model in use saw. The best candidate is refitted on everything and replaces the model only
if it beats both the model in use and the calibration alone on them by 3%, and is less than half
as far off as one fixed guess (the clicks' own middle). The last test is what stops clicks made
without looking: fitted to labels that have nothing to do with the eyes, a model learns to point at
the middle of the screen, which can beat a good model on such labels but never beats the fixed
guess. A run of bad labels therefore cannot make tracking worse; at worst nothing changes.

The settings (``Settings``: the gate, the half-life, the penalties of the head terms and the
resting direction, how many clicks before the head terms, how often to fit, the lines' weight)
were chosen on MPIIFaceGaze; ``gaze.tune`` replays your own clicks to choose them for you and
writes ``gaze-tuned.json``, which is read again before each fit.

Proof that it works, live: every click is predicted before anything learns from it, so its error
at that moment is an honest test of the model in use. Each click is stored with that error, the
error the calibration alone would have made on it (``base_px``), and the model version in use, so
the eye-tracking dashboard shows, on clicks no model had seen, whether learning beats the
calibration and by how much (their ratio). Each update is logged when it is fitted and again when
the tracker first predicts a frame with it (``applied``), normally one frame later.

Stored in ``vault/calibration``: ``clicks.jsonl`` (time, clicked pixel, the frame's head pose and
eye measurements as numbers, the errors at that moment, the model version), ``lines.jsonl`` (the
same for reading-line labels, with the line's extent), ``learning.jsonl`` (each fit: the held-out
errors, whether it was taken; each update's first frame; each tuning) and ``gaze-learned.json``
(the learned weights, for this calibration only). No images, no text, nothing about what was
clicked or read. A new calibration starts over: labels from before it are not used.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import threading
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .head import POSE_TERMS, GazeModel, pose_terms, unpack

log = logging.getLogger("gaze.learn")
LOOK_BEFORE_S = 0.5  # the steady look must end within this long before the click
STILL_S = 0.08  # and last at least this long
STILL_DISPERSION = 0.05  # max (x range + y range) of a steady look's raw gaze, as a share of the screen width
RADIUS_OF_DIAGONAL = 1 / 12  # a click farther than this from the model's gaze was made without looking,
GATE_MAX_OF_DIAGONAL = 1 / 3  # ... or than gate_k times the recent median error, up to this
GATE_CLICKS = 100
MIN_CLICKS = 30  # usable clicks before the first fit
REFIT_LINES = 200  # usable reading lines between fits (a fit is judged by the clicks since the last update)
HOLDOUT = 0.2  # at least this share of the newest clicks is held out to judge a fit
MIN_HELD_OUT = 10
MARGIN = 0.97  # a candidate must beat the model in use and the calibration by 3%
GUESS_SHARE = 0.5  # ... and be less than half as far off as a fixed guess at the clicks' middle
PENALTIES = (0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0)
MAX_CLICKS = 3000  # kept in memory and in the file: the newest
MAX_LINES = 3000
LOOKS = 400  # recent fixations kept for the receiver's line labels (a few minutes of reading)
LOOK_MIN_FRAMES = 3
BUFFER_S = 2.0  # frames kept: the half second before a click, and the longest fixation (1 s)
CLICKS_FILE = "clicks.jsonl"
LINES_FILE = "lines.jsonl"
LEARNED_FILE = "gaze-learned.json"
HISTORY_FILE = "learning.jsonl"
TUNED_FILE = "gaze-tuned.json"
LIVE_CLICKS = 300  # clicks the live accuracy by model version is measured on
TUNE_CHECK_S = 600.0


@dataclass(frozen=True)
class Settings:
    """What learning can be tuned on (``gaze.tune``); the defaults were chosen on MPIIFaceGaze."""

    gate_k: float = 3.0  # a label beyond this many times the recent median error was made without looking
    half_life_days: float = 7.0  # older labels count half as much after this long
    intercept_penalty: float = 0.1  # the eye's resting direction moves more freely than its gains
    pose_penalty: float = 1.0  # the head terms' ridge penalty, relative to the eye terms' (per cm, per 0.1 rad)
    pose_min_clicks: int = 60  # clicks a fit needs before it also learns what the head's place and turn do
    refit_every: int = 20  # usable clicks between fits
    line_weight: float = 0.25  # a reading-line label counts this much of a click in a fit (0: unused)

    BOUNDS = {  # noqa: RUF012 - the range each setting may take, also the tuner's
        "gate_k": (1.5, 6.0), "half_life_days": (0.5, 60.0), "intercept_penalty": (0.01, 3.0),
        "pose_penalty": (0.1, 10.0), "pose_min_clicks": (30, 400), "refit_every": (5, 100),
        "line_weight": (0.0, 1.0),
    }  # fmt: skip

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Any) -> Settings:
        """Settings from a file's numbers; unknown keys are ignored and values held to ``BOUNDS``."""
        if not isinstance(data, dict):
            return cls()
        values = {}
        for f in fields(cls):
            value = data.get(f.name)
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
                continue
            lo, hi = cls.BOUNDS[f.name]
            value = min(max(value, lo), hi)
            values[f.name] = int(round(value)) if f.type in (int, "int") else float(value)
        return replace(cls(), **values)


@dataclass
class Click:
    t: float  # epoch seconds
    x: float  # monitor pixels
    y: float
    X: np.ndarray  # the look's mean frame vector (gaze.head.pack)
    error_px: float  # the model in use's error on it when it was made, before learning from it
    base_px: float = math.nan  # the calibration alone's error on it
    version: int = 0  # the model version in use (0: the calibration)
    used: bool | None = None  # whether it was within the gate when made (None: not recorded)
    kind: str = "click"  # or "line": a line being read
    span: tuple[float, float] | None = None  # a line's horizontal extent (monitor px)

    def to_json(self) -> dict:
        out = {"t": round(self.t, 3), "x": round(self.x, 1), "y": round(self.y, 1),
               "X": [round(float(v), 6) for v in self.X], "error_px": _round(self.error_px),
               "base_px": _round(self.base_px), "v": self.version, "used": self.used}  # fmt: skip
        if self.span is not None:
            out["span"] = [round(self.span[0], 1), round(self.span[1], 1)]
        return out

    @classmethod
    def from_json(cls, data: dict, kind: str = "click") -> Click:
        span = data.get("span")
        return cls(float(data["t"]), float(data["x"]), float(data["y"]), np.asarray(data["X"], dtype=float),
                   _number(data.get("error_px")), _number(data.get("base_px")), int(data.get("v") or 0),
                   data.get("used"), kind, (float(span[0]), float(span[1])) if span else None)  # fmt: skip


def steady(frames: list[tuple[float, np.ndarray, float, float]], width: float) -> list[int] | None:
    """The indices of the last steady look among ``frames`` ((time, vector, x, y), oldest first):
    the latest run of consecutive frames lasting ``STILL_S`` or more whose raw gaze stays within
    ``STILL_DISPERSION`` of the screen width (x range plus y range), as long as it can be."""
    limit = STILL_DISPERSION * width
    for end in range(len(frames) - 1, -1, -1):
        start = end
        xs, ys = [frames[end][2]], [frames[end][3]]
        while start > 0:
            x, y = frames[start - 1][2], frames[start - 1][3]
            if (max(xs + [x]) - min(xs + [x])) + (max(ys + [y]) - min(ys + [y])) > limit:
                break
            xs.append(x)
            ys.append(y)
            start -= 1
        if frames[end][0] - frames[start][0] >= STILL_S:
            return list(range(start, end + 1))
    return None


class Learner:
    """Keeps the last seconds of frames, turns clicks and the lines being read into calibration
    points, and refits the head-aware model's eye regression from them (``estimator``: a
    ``gaze.head.HeadEstimator``, or anything with its ``model`` and ``predict``)."""

    def __init__(
        self,
        estimator: Any,
        monitor: tuple[int, int],
        folder: Path | None,
        calibration: str | None,
        *,
        settings: Settings | None = None,
        min_clicks: int = MIN_CLICKS,
        background: bool = False,
        head_terms: bool = True,
    ) -> None:
        self.estimator = estimator
        self.width, self.height = float(monitor[0]), float(monitor[1])
        diagonal = math.hypot(self.width, self.height)
        self.radius_min, self.radius_max = RADIUS_OF_DIAGONAL * diagonal, GATE_MAX_OF_DIAGONAL * diagonal
        self.radius = self.radius_min
        self.folder, self.calibration = folder, calibration
        self.min_clicks = min_clicks
        # Settings given are kept; otherwise the tuned ones, read again before each fit.
        self.fixed_settings = settings is not None
        self.settings = settings or Settings()
        self._tuned_stamp: tuple[int, int] | None = None
        self.prior = [(m.copy(), sd.copy(), w.copy()) for m, sd, w in estimator.model.eyes]  # the calibration's
        self.head_terms = head_terms
        self.pose_ref: tuple[np.ndarray, np.ndarray] | None = estimator.model.pose_ref
        self.frames: deque[tuple[float, np.ndarray, float, float]] = deque()
        self.looks: OrderedDict[str, tuple[float, float, np.ndarray]] = OrderedDict()  # fixation id -> look
        self.clicks: list[Click] = []
        self.lines: list[Click] = []
        self.skipped = {"no steady look": 0, "not looking": 0, "off the monitor": 0}
        self.line_counts = {"used": 0, "off the line": 0, "fixation not found": 0}
        self.since_fit = self.since_lines = 0
        self.version = 0
        self.fit: dict | None = None  # the last fit's numbers, for state()
        self.updated: str | None = None
        self.updated_at: float | None = None  # wall clock of the last update (epoch seconds)
        self.applied: dict | None = None  # the update the tracker's frames last started using
        self.learned_until = -math.inf  # the newest click the model in use learned from
        # In the tracker a fit runs on its own thread (tenths of a second with thousands of labels),
        # so frames never wait for it; ``_lock`` guards the labels it reads.
        self.background = background
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._tuner: Any = None  # the tuning process (gaze.tune), while it runs
        self._tune_check_at = -math.inf
        self.read_settings()
        self._load(_epoch(calibration))

    # -- evidence ------------------------------------------------------------------------------------

    def frame(self, t: float, vector: np.ndarray, x: float, y: float) -> None:
        """A frame's vector and the model's raw gaze for it (monitor pixels, before smoothing)."""
        self.frames.append((t, np.array(vector, dtype=float), x, y))
        while self.frames and t - self.frames[0][0] > BUFFER_S:
            self.frames.popleft()

    def click(self, t: float, x: float, y: float) -> str:
        """A click at monitor pixel (x, y): "used", or why not."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            self.skipped["off the monitor"] += 1
            return "off the monitor"
        frames = [f for f in self.frames if t - LOOK_BEFORE_S <= f[0] <= t]
        look = steady(frames, self.width)
        if look is None:
            self.skipped["no steady look"] += 1
            return "no steady look"
        return self.add(t, x, y, np.mean([frames[i][1] for i in look], axis=0))

    def fixation(self, fixation_id: str, t0: float, t1: float) -> None:
        """A fixation the tracker sent the receiver: its frames' mean vector is kept, in case the
        receiver later says which line it was on."""
        frames = [f[1] for f in self.frames if t0 <= f[0] <= t1]
        if len(frames) < LOOK_MIN_FRAMES:
            return
        self.looks[fixation_id] = (t0, t1, np.mean(frames, axis=0))
        while len(self.looks) > LOOKS:
            self.looks.popitem(last=False)

    def line(self, fixation_id: str, y: float, x0: float, x1: float) -> str:
        """The receiver read fixation ``fixation_id`` on a line whose middle is at height ``y``,
        spanning ``x0``-``x1`` (monitor pixels): "used", or why not."""
        look = self.looks.pop(fixation_id, None)
        if look is None:
            self.line_counts["fixation not found"] += 1
            return "fixation not found"
        return self.add_line(look[0], y, x0, x1, look[2])

    def add(self, t: float, x: float, y: float, vector: np.ndarray) -> str:
        """A click at (x, y) with the look that preceded it: "used", or "not looking"."""
        click = self._label(t, x, y, vector, "click")
        with self._lock:
            self.clicks.append(click)
            if len(self.clicks) > MAX_CLICKS:
                del self.clicks[: len(self.clicks) - MAX_CLICKS]
        if len(self.clicks) % 20 == 0 and not self.busy():
            self._gate()
        click.used = click.error_px <= self.radius
        self._save(CLICKS_FILE, click)
        if not click.used:
            self.skipped["not looking"] += 1
            return "not looking"
        self.since_fit += 1
        if self.since_fit >= self.settings.refit_every:
            self._start_refit()
        return "used"

    def add_line(self, t: float, y: float, x0: float, x1: float, vector: np.ndarray) -> str:
        """A look on a line being read (monitor pixels): "used", or "off the line" (the model's gaze
        farther from it than a click may be)."""
        vector = np.asarray(vector, dtype=float)
        predicted = self.estimator.predict(vector[None, :])[0]
        if not np.isfinite(predicted).all():
            self.line_counts["off the line"] += 1
            return "off the line"
        lo, hi = min(x0, x1), max(x0, x1)
        label = self._label(t, min(max(float(predicted[0]), lo), hi), y, vector, "line", predicted)
        label.span = (lo, hi)
        label.used = label.error_px <= self.radius
        with self._lock:
            self.lines.append(label)
            if len(self.lines) > MAX_LINES:
                del self.lines[: len(self.lines) - MAX_LINES]
        self._save(LINES_FILE, label)
        if not label.used:
            self.line_counts["off the line"] += 1
            return "off the line"
        self.line_counts["used"] += 1
        self.since_lines += 1
        if self.since_lines >= REFIT_LINES and self.settings.line_weight > 0:
            self._start_refit()
        return "used"

    def _label(
        self, t: float, x: float, y: float, vector: np.ndarray, kind: str, predicted: np.ndarray | None = None
    ) -> Click:
        vector = np.asarray(vector, dtype=float)
        version = self.version  # an update landing on the worker thread just now counts from the next label
        if predicted is None:
            predicted = self.estimator.predict(vector[None, :])[0]
        error = float(np.hypot(predicted[0] - x, predicted[1] - y))
        label = Click(
            t, float(x), float(y), vector, error if math.isfinite(error) else math.inf, version=version, kind=kind
        )
        # What the calibration alone would have made of it: the baseline learning is judged against.
        label.base_px = label.error_px if version == 0 else float(self.errors([label], self.prior)[0])
        return label

    def _start_refit(self) -> None:
        if self.busy():
            return
        if self.background:
            self._worker = threading.Thread(target=self._refit_logged, name="learn from clicks", daemon=True)
            self._worker.start()
        else:
            self.refit()

    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def maybe_tune(self, now: float) -> bool:
        """Start ``gaze.tune`` on this calibration's clicks in a process of its own when it is due
        (checked every 10 minutes); its settings are read before the next fit."""
        if self.fixed_settings or self.folder is None or now < self._tune_check_at:
            return False
        self._tune_check_at = now + TUNE_CHECK_S
        if self._tuner is not None and self._tuner.poll() is None:
            return False  # still running
        from . import tune

        if not tune.due(self.folder, self.snapshot(), now):
            return False
        try:
            self._tuner = tune.start(self.folder.parent)
        except OSError as error:
            log.warning("could not start tuning: %s", error)
            return False
        log.info("tuning learning on %d clicks in the background", len(self.clicks))
        return True

    def _refit_logged(self) -> None:
        try:
            fit = self.refit()
            if fit and fit["accepted"]:
                log.info("learned from %d clicks and %d lines: %s", fit["clicks"], fit["lines"], fit["error_px"])
        except Exception:  # noqa: BLE001 - learning must never stop tracking
            log.exception("learning from clicks failed")

    # -- learning ------------------------------------------------------------------------------------

    def snapshot(self) -> list[Click]:
        with self._lock:
            return list(self.clicks)

    def line_snapshot(self) -> list[Click]:
        with self._lock:
            return list(self.lines)

    def read_settings(self) -> None:
        """The tuned settings (``gaze-tuned.json``), when they changed since last read."""
        if self.fixed_settings or self.folder is None:
            return
        path = self.folder / TUNED_FILE
        try:
            stat = path.stat()
        except OSError:
            return
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._tuned_stamp:
            return
        self._tuned_stamp = stamp
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        settings = Settings.from_json(data.get("settings") if isinstance(data, dict) else None)
        if settings != self.settings:
            log.info("learning with tuned settings: %s", settings)
            self.settings = settings

    def _gate(self) -> None:
        """How far from the gaze a label may be and still count as looked at: 1/12 of the diagonal
        (PACE), or ``gate_k`` times the model's median error on the latest clicks when the model is
        coarser than that (else a coarse calibration would reject nearly every click and never
        improve), at most a third of the diagonal."""
        recent = self.snapshot()[-GATE_CLICKS:]
        if len(recent) < 20:
            return
        typical = float(np.median(self.errors(recent, self.estimator.model.eyes)))
        self.radius = min(max(self.radius_min, self.settings.gate_k * typical), self.radius_max)

    def usable(self, model: GazeModel | None = None, labels: list[Click] | None = None) -> list[Click]:
        """Labels (the clicks, unless given) the model in use puts within the radius (checked again
        after each update)."""
        labels = self.snapshot() if labels is None else labels
        if not labels:
            return []
        errors = self.errors(labels, (model or self.estimator.model).eyes)
        return [c for c, e in zip(labels, errors, strict=True) if e <= self.radius]

    def errors(self, clicks: list[Click], eyes: list) -> np.ndarray:
        """Each label's distance (monitor pixels) from the gaze a model with these eye weights
        predicts for its look; inf where the gaze misses the screen."""
        X = np.array([c.X for c in clicks])
        model = GazeModel(self.estimator.model.screen, eyes=eyes, pose_ref=self.pose_ref)
        p = model.predict_arrays(
            X[:, :9].reshape(-1, 3, 3), X[:, 12:18].reshape(-1, 2, 3), X[:, 18:].reshape(len(X), 2, -1)
        )
        e = np.hypot(p[:, 0] - [c.x for c in clicks], p[:, 1] - [c.y for c in clicks])
        return np.where(np.isfinite(e), e, np.inf)

    def solve(self, labels: list[Click], penalty: float, now: float) -> list:
        """Eye weights fitted to ``labels`` (clicks, and lines at ``line_weight``), pulled toward
        the calibration's by ``penalty``.

        A line says how high the eyes were, not where along it: its horizontal target is where the
        model being fitted puts the gaze, held to the line's extent, recomputed at every round from
        the previous round's weights (starting from the model in use). So a line pulls the
        horizontal only when the gaze falls off its ends, and never toward where an older model
        once put it."""
        s = self.settings
        X = np.array([c.X for c in labels])
        heads = unpack(X)
        R, centres, F = X[:, :9].reshape(-1, 3, 3), X[:, 12:18].reshape(-1, 2, 3), X[:, 18:].reshape(len(X), 2, -1)
        targets = np.array([[c.x, c.y] for c in labels])
        along = np.array([c.kind == "line" and c.span is not None for c in labels])
        span = np.array([c.span if c.span is not None else (0.0, 0.0) for c in labels])
        pose = None
        if self.head_terms and sum(c.kind == "click" for c in labels) >= s.pose_min_clicks:
            if self.pose_ref is None:  # measured from where the head was over the first labels, then kept
                u, _, vt = np.linalg.svd(R.mean(0))
                self.pose_ref = (u @ vt, centres.mean(1).mean(0))
            pose = pose_terms(R, centres, self.pose_ref)
        age = now - np.array([c.t for c in labels])
        kind = np.where(along | np.array([c.kind == "line" for c in labels]), s.line_weight, 1.0)
        weight = kind * 0.5 ** (np.maximum(age, 0.0) / (s.half_life_days * 86400.0))
        screen = self.estimator.model.screen

        def predict(eyes: list) -> np.ndarray:
            return GazeModel(screen, eyes=eyes, pose_ref=self.pose_ref).predict_arrays(R, centres, F)

        across = np.ones(len(labels))  # how much each label says about the horizontal

        def aim(predicted: np.ndarray) -> np.ndarray:
            if along.any():
                x = np.where(np.isfinite(predicted[:, 0]), predicted[:, 0], targets[:, 0])
                held = np.clip(x[along], span[along, 0], span[along, 1])
                targets[along, 0] = held
                # Within its extent a line says nothing about the horizontal; off its ends, that the
                # gaze was at least that far in.
                across[along] = (np.abs(held - x[along]) > 0.5).astype(float)
            return self.estimator.model.required(heads, targets)  # (n, 2 eyes, 2)

        wanted = (
            aim(predict(self.estimator.model.eyes)) if along.any() else self.estimator.model.required(heads, targets)
        )
        eyes = self._weighted(F, pose, wanted, weight, penalty, across)
        for _ in range(2):  # Huber: labels still far off after a fit count less in the next
            predicted = predict(eyes)
            wanted = aim(predicted)
            e = np.hypot(*(predicted - targets).T)
            finite = e[np.isfinite(e)]
            if not len(finite):
                break
            c = 1.5 * max(float(np.median(finite)), 1.0)
            robust = np.where(np.isfinite(e), np.minimum(1.0, c / np.maximum(e, 1e-9)), 0.0)
            eyes = self._weighted(F, pose, wanted, weight * robust, penalty, across)
        return eyes

    def _weighted(
        self,
        F: np.ndarray,
        pose: np.ndarray | None,
        wanted: np.ndarray,
        weight: np.ndarray,
        penalty: float,
        across: np.ndarray | None = None,
    ) -> list:
        """Each eye's weights: the eye terms pulled toward the calibration's, the head terms (when
        ``pose`` is given) toward zero, since the calibration, made with the head still, has none.
        ``across`` scales each label's weight for the horizontal component of the direction (a
        line within its extent: 0), which is then solved on its own."""
        s = self.settings
        eyes = []
        for e, (mean, sd, W0) in enumerate(self.prior):
            columns = [np.ones((len(F), 1)), (F[:, e] - mean) / sd]
            shrink = [s.intercept_penalty] + [1.0] * F.shape[-1]
            prior = W0
            if pose is not None:
                columns.append(pose)
                shrink += [s.pose_penalty] * POSE_TERMS
                prior = np.vstack([W0, np.zeros((POSE_TERMS, W0.shape[1]))])
            Z = np.hstack(columns)
            P = penalty * np.diag(shrink)
            A = Z.T @ (weight[:, None] * Z) + P
            B = Z.T @ (weight[:, None] * wanted[:, e]) + P @ prior
            W = np.linalg.solve(A, B)
            if across is not None and not np.all(across == 1.0):
                w = weight * across
                W[:, 0] = np.linalg.solve(Z.T @ (w[:, None] * Z) + P, Z.T @ (w * wanted[:, e, 0]) + P @ prior[:, 0])
            eyes.append((mean, sd, W))
        return eyes

    def refit(self, now: float | None = None) -> dict | None:
        """Fit candidates on the older clicks and the lines before them, judge them on the newest
        clicks, and take the best if it beats the model in use and the calibration. Returns the
        fit's numbers (None: too few)."""
        self.since_fit = self.since_lines = 0
        self.read_settings()
        self._gate()
        clicks = sorted(self.usable(), key=lambda c: c.t)
        unseen = [c for c in clicks if c.t > self.learned_until]
        if len(clicks) < self.min_clicks or len(unseen) < MIN_HELD_OUT:
            return None
        lines = (
            sorted(self.usable(labels=self.line_snapshot()), key=lambda c: c.t) if self.settings.line_weight > 0 else []
        )
        now = now if now is not None else max(clicks[-1].t, lines[-1].t if lines else -math.inf)
        test = unseen[-max(MIN_HELD_OUT, int(HOLDOUT * len(clicks))) :]
        train = clicks[: len(clicks) - len(test)]
        if len(train) < MIN_HELD_OUT:
            return None
        # Candidates learn only from what came before the held-out clicks, as they would in use.
        train += [line for line in lines if line.t < test[0].t]
        current = float(np.median(self.errors(test, self.estimator.model.eyes)))
        calibration = float(np.median(self.errors(test, self.prior)))
        best, best_error = None, math.inf
        for penalty in PENALTIES:
            error = float(np.median(self.errors(test, self.solve(train, penalty, test[0].t))))
            if error < best_error:
                best, best_error = penalty, error
        labels = np.array([[c.x, c.y] for c in test])
        guess = float(np.median(np.hypot(*(labels - np.median(labels, axis=0)).T)))
        accepted = (
            best is not None and best_error < MARGIN * min(current, calibration) and best_error < GUESS_SHARE * guess
        )
        self.fit = {
            "clicks": len(clicks), "lines": len(lines), "penalty": best, "accepted": accepted,
            "error_px": {"calibration": round(calibration, 1), "in_use": round(current, 1),
                         "learned": round(best_error, 1), "fixed_guess": round(guess, 1)},
        }  # fmt: skip
        if accepted:
            eyes = self.solve(clicks + lines, best, now)
            model = self.estimator.model
            model.pose_ref = self.pose_ref  # set once, before any weights that use it
            model.eyes = eyes  # one reference: the next frame predicts with all of the new weights
            self.learned_until = clicks[-1].t
            self.version += 1
            model.version = self.version
            self.updated = datetime.fromtimestamp(now, UTC).isoformat()
            self.updated_at = time.time()
            self._save_model()
        self.fit["version"] = self.version
        self.fit["head_terms"] = self.head_terms and len(clicks) >= self.settings.pose_min_clicks
        self._history({"event": "fit", "t": datetime.fromtimestamp(now, UTC).isoformat(), "held_out": len(test),
                       **self.fit})  # fmt: skip
        return self.fit

    def started(self, version: int, t: float) -> None:
        """The tracker's first frame predicted with model ``version`` at ``t`` (epoch seconds)."""
        after = None if self.updated_at is None or version != self.version else (t - self.updated_at) * 1000
        self.applied = {
            "version": version, "t": datetime.fromtimestamp(t, UTC).isoformat(),
            "after_ms": None if after is None else round(max(after, 0.0), 1),
        }  # fmt: skip
        self._history({"event": "applied", **self.applied})

    # -- reporting and files ---------------------------------------------------------------------------

    def state(self) -> dict:
        clicks = self.snapshot()
        recent = [c.error_px for c in clicks[-50:] if math.isfinite(c.error_px)]
        eyes = self.estimator.model.eyes
        return {
            "version": self.version,
            # Whether the model in use learned what the head's place, distance and turn do too.
            "head_terms": bool(eyes) and eyes[0][2].shape[0] > 1 + len(eyes[0][0]),
            "clicks": len(clicks),
            "skipped": dict(self.skipped),
            "lines": dict(self.line_counts) | {"kept": len(self.lines)},
            "recent_error_px": round(float(np.median(recent)), 1) if recent else None,
            "live": live(clicks[-LIVE_CLICKS:]),
            "fit": self.fit,
            "updated": self.updated,
            "applied": self.applied,
            "settings": self.settings.to_json(),
            "tuned": self._tuned_stamp is not None and self.settings != Settings(),
        }

    def _save(self, name: str, label: Click) -> None:
        if self.folder is None:
            return
        try:
            with (self.folder / name).open("a", encoding="utf-8") as out:
                out.write(json.dumps(label.to_json()) + "\n")
        except OSError:
            pass

    def _history(self, event: dict) -> None:
        history(self.folder, event)

    def _save_model(self) -> None:
        if self.folder is None:
            return
        data = {
            "calibration": self.calibration, "version": self.version, "t": self.updated, "fit": self.fit,
            "learned_until": self.learned_until,
            "pose_ref": None if self.pose_ref is None else {"R": self.pose_ref[0].tolist(), "p": self.pose_ref[1].tolist()},
            "eyes": [{"mean": m.tolist(), "sd": sd.tolist(), "weights": w.tolist()} for m, sd, w in self.estimator.model.eyes],
        }  # fmt: skip
        path = self.folder / LEARNED_FILE
        try:
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data) + "\n", encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass

    def _load(self, since: float | None) -> None:
        """Labels since this calibration, and what was learned from them for it."""
        if self.folder is None:
            return
        self.clicks = load_labels(self.folder / CLICKS_FILE, since, self.clicks_shape(), "click", MAX_CLICKS)
        self.lines = load_labels(self.folder / LINES_FILE, since, self.clicks_shape(), "line", MAX_LINES)
        learned = self.folder / LEARNED_FILE
        try:
            data = json.loads(learned.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("calibration") != self.calibration or len(data.get("eyes", [])) != len(self.prior):
            return  # learned for another calibration
        eyes = [(np.array(e["mean"]), np.array(e["sd"]), np.array(e["weights"])) for e in data["eyes"]]
        ref = data.get("pose_ref")
        extra = POSE_TERMS if ref else 0
        if any(
            w.shape != (p[2].shape[0] + extra, p[2].shape[1]) for (_, _, w), p in zip(eyes, self.prior, strict=True)
        ):
            return
        if ref:
            self.pose_ref = (np.array(ref["R"]), np.array(ref["p"]))
            self.estimator.model.pose_ref = self.pose_ref
        self.estimator.model.eyes = eyes
        self.version, self.updated, self.fit = int(data.get("version", 1)), data.get("t"), data.get("fit")
        self.estimator.model.version = self.version
        self.learned_until = float(data.get("learned_until", -math.inf))

    def clicks_shape(self) -> tuple[int]:
        mean = self.prior[0][0]
        return (18 + 2 * len(mean),)


def load_labels(path: Path, since: float | None, shape: tuple[int], kind: str, keep: int) -> list[Click]:
    """The newest ``keep`` labels of a labels file made since ``since`` (epoch seconds) with frame
    vectors of ``shape``; the file is rewritten to them when it has grown to twice that."""
    if not path.is_file():
        return []
    rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
    labels = []
    for row in rows:
        try:
            label = Click.from_json(json.loads(row), kind)
        except (ValueError, KeyError, TypeError, IndexError):
            continue
        if (since is None or label.t >= since) and label.X.shape == shape:
            labels.append(label)
    labels = labels[-keep:]
    if len(rows) > 2 * keep:  # keep the file to what is used
        with contextlib.suppress(OSError):
            path.write_text("".join(json.dumps(c.to_json()) + "\n" for c in labels), encoding="utf-8")
    return labels


def history(folder: Path | None, event: dict) -> None:
    """Append an event to ``learning.jsonl`` (best effort)."""
    if folder is None:
        return
    try:
        with (folder / HISTORY_FILE).open("a", encoding="utf-8") as out:
            out.write(json.dumps(event) + "\n")
    except OSError:
        pass


def live(clicks: list[Click]) -> dict | None:
    """Live accuracy on clicks the model in use had not learned from: the median error of the
    model in use and of the calibration alone on the same clicks, their ratio (under 1: learning
    helps), and the median error by model version."""
    rows = [
        (c.error_px, c.base_px, c.version) for c in clicks if math.isfinite(c.error_px) and math.isfinite(c.base_px)
    ]
    if not rows:
        return None
    errors = np.array([r[:2] for r in rows])
    model, base = (float(v) for v in np.median(errors, axis=0))
    versions = np.array([r[2] for r in rows])
    return {
        "clicks": len(rows), "error_px": round(model, 1), "calibration_px": round(base, 1),
        "ratio": round(model / base, 3) if base > 0 else None,
        "by_version": [{"version": int(v), "clicks": int((versions == v).sum()),
                        "error_px": round(float(np.median(errors[versions == v, 0])), 1),
                        "calibration_px": round(float(np.median(errors[versions == v, 1])), 1)}
                       for v in np.unique(versions)],
    }  # fmt: skip


def _round(value: float) -> float | None:
    return round(value, 1) if math.isfinite(value) else None


def _number(value: Any) -> float:
    return math.nan if value is None else float(value)


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None

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

How it learns: for each click the head pose gives the direction each eye must have had in the head
to look at the clicked point (``GazeModel.required``). The loss is the squared difference between
that direction and the model's, so it is the click's error on screen seen through the geometry. The
eye regression is refitted toward the clicks, starting from the calibrated weights (a ridge
penalty pulls toward them, not toward zero), with newer clicks weighted more (half-life a week)
and clicks far off after a first fit weighted less (two Huber reweightings).

When it changes the model: every ``REFIT_EVERY`` usable clicks it holds out the newest clicks that
the model in use has not learned from (at least 10, and at least 20% of all), fits a candidate on
the rest for each penalty strength, and measures the median error in screen pixels on the held-out
clicks, which neither the candidates nor the model in use saw. The best candidate is refitted on
all clicks and replaces the model only if it beats both the model in use and the calibration alone
on them by 3%, and is less than half as far off as one fixed guess (the clicks' own middle). The
last test is what stops clicks made without looking: fitted to labels that have nothing to do with
the eyes, a model learns to point at the middle of the screen, which can beat a good model on such
labels but never beats the fixed guess. A run of bad clicks therefore cannot make tracking worse;
at worst nothing changes.

Stored: ``vault/calibration/clicks.jsonl`` (time, clicked pixel, the frame's head pose and eye
measurements as numbers, the error at that moment) and ``gaze-learned.json`` (the learned
weights, for this calibration only). No images, nothing about what was clicked. A new calibration
starts over: clicks from before it are not used.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .head import GazeModel, unpack

log = logging.getLogger("gaze.learn")
LOOK_BEFORE_S = 0.5  # the steady look must end within this long before the click
STILL_S = 0.08  # and last at least this long
STILL_DISPERSION = 0.05  # max (x range + y range) of a steady look's raw gaze, as a share of the screen width
RADIUS_OF_DIAGONAL = 1 / 12  # a click farther than this from the model's gaze was made without looking,
GATE_K = 3.0  # ... or than this many times the model's median error on recent clicks, if larger
GATE_MAX_OF_DIAGONAL = 1 / 3
GATE_CLICKS = 100
REFIT_EVERY = 20  # usable clicks between fits
MIN_CLICKS = 30  # usable clicks before the first fit
HOLDOUT = 0.2  # at least this share of the newest clicks is held out to judge a fit
MIN_HELD_OUT = 10
MARGIN = 0.97  # a candidate must beat the model in use and the calibration by 3%
GUESS_SHARE = 0.5  # ... and be less than half as far off as a fixed guess at the clicks' middle
HALF_LIFE_S = 7 * 86400.0
PENALTIES = (0.3, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0)
INTERCEPT_PENALTY = 0.1  # the eye's resting direction may move more freely than its gains
MAX_CLICKS = 3000  # kept in memory and in the file: the newest
BUFFER_S = 1.0
CLICKS_FILE = "clicks.jsonl"
LEARNED_FILE = "gaze-learned.json"


@dataclass
class Click:
    t: float  # epoch seconds
    x: float  # monitor pixels
    y: float
    X: np.ndarray  # the steady look's mean frame vector (gaze.head.pack)
    error_px: float  # the model's error on it when it was clicked

    def to_json(self) -> dict:
        return {"t": round(self.t, 3), "x": self.x, "y": self.y, "X": [round(float(v), 6) for v in self.X],
                "error_px": round(self.error_px, 1)}  # fmt: skip

    @classmethod
    def from_json(cls, data: dict) -> Click:
        return cls(float(data["t"]), float(data["x"]), float(data["y"]), np.asarray(data["X"], dtype=float),
                   float(data.get("error_px", math.nan)))  # fmt: skip


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
    """Keeps the last second of frames, turns clicks into calibration points, and refits the
    head-aware model's eye regression from them (``estimator``: a ``gaze.head.HeadEstimator``)."""

    def __init__(
        self,
        estimator: Any,
        monitor: tuple[int, int],
        folder: Path | None,
        calibration: str | None,
        *,
        refit_every: int = REFIT_EVERY,
        min_clicks: int = MIN_CLICKS,
        background: bool = False,
    ) -> None:
        self.estimator = estimator
        self.width, self.height = float(monitor[0]), float(monitor[1])
        diagonal = math.hypot(self.width, self.height)
        self.radius_min, self.radius_max = RADIUS_OF_DIAGONAL * diagonal, GATE_MAX_OF_DIAGONAL * diagonal
        self.radius = self.radius_min
        self.gate_k = GATE_K
        self.folder, self.calibration = folder, calibration
        self.refit_every, self.min_clicks = refit_every, min_clicks
        self.prior = [(m.copy(), sd.copy(), w.copy()) for m, sd, w in estimator.model.eyes]  # the calibration's
        self.frames: deque[tuple[float, np.ndarray, float, float]] = deque()
        self.clicks: list[Click] = []
        self.skipped = {"no steady look": 0, "not looking": 0, "off the monitor": 0}
        self.since_fit = 0
        self.version = 0
        self.fit: dict | None = None  # the last fit's numbers, for state()
        self.updated: str | None = None
        self.learned_until = -math.inf  # the newest click the model in use learned from
        # In the tracker a fit runs on its own thread (tenths of a second with thousands of clicks),
        # so frames never wait for it; ``_lock`` guards the clicks it reads.
        self.background = background
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        since = _epoch(calibration)
        self._load(since)

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

    def add(self, t: float, x: float, y: float, vector: np.ndarray) -> str:
        """A click at (x, y) with the look that preceded it: "used", or "not looking"."""
        predicted = self.estimator.predict(np.asarray(vector, dtype=float)[None, :])[0]
        error = float(np.hypot(predicted[0] - x, predicted[1] - y))
        click = Click(
            t, float(x), float(y), np.asarray(vector, dtype=float), error if math.isfinite(error) else math.inf
        )
        with self._lock:
            self.clicks.append(click)
            if len(self.clicks) > MAX_CLICKS:
                del self.clicks[: len(self.clicks) - MAX_CLICKS]
        self._save_click(click)
        if len(self.clicks) % 20 == 0 and not self.busy():
            self._gate()
        if not click.error_px <= self.radius:
            self.skipped["not looking"] += 1
            return "not looking"
        self.since_fit += 1
        if self.since_fit >= self.refit_every and not self.busy():
            if self.background:
                self._worker = threading.Thread(target=self._refit_logged, name="learn from clicks", daemon=True)
                self._worker.start()
            else:
                self.refit()
        return "used"

    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _refit_logged(self) -> None:
        try:
            fit = self.refit()
            if fit and fit["accepted"]:
                log.info("learned from %d clicks: %s", fit["clicks"], fit["error_px"])
        except Exception:  # noqa: BLE001 - learning must never stop tracking
            log.exception("learning from clicks failed")

    # -- learning ------------------------------------------------------------------------------------

    def snapshot(self) -> list[Click]:
        with self._lock:
            return list(self.clicks)

    def _gate(self) -> None:
        """How far from the gaze a click may be and still count as looked at: 1/12 of the diagonal
        (PACE), or ``GATE_K`` times the model's median error on the latest clicks when the model is
        coarser than that (else a coarse calibration would reject nearly every click and never
        improve), at most a third of the diagonal."""
        recent = self.snapshot()[-GATE_CLICKS:]
        if len(recent) < 20:
            return
        typical = float(np.median(self.errors(recent, self.estimator.model.eyes)))
        self.radius = min(max(self.radius_min, self.gate_k * typical), self.radius_max)

    def usable(self, model: GazeModel | None = None) -> list[Click]:
        """Clicks the model in use puts within the radius (checked again after each update)."""
        clicks = self.snapshot()
        if not clicks:
            return []
        errors = self.errors(clicks, (model or self.estimator.model).eyes)
        return [c for c, e in zip(clicks, errors, strict=True) if e <= self.radius]

    def errors(self, clicks: list[Click], eyes: list) -> np.ndarray:
        """Each click's distance (monitor pixels) from the gaze a model with these eye weights
        predicts for its look; inf where the gaze misses the screen."""
        X = np.array([c.X for c in clicks])
        model = GazeModel(self.estimator.model.screen, eyes=eyes)
        p = model.predict_arrays(
            X[:, :9].reshape(-1, 3, 3), X[:, 12:18].reshape(-1, 2, 3), X[:, 18:].reshape(len(X), 2, -1)
        )
        e = np.hypot(p[:, 0] - [c.x for c in clicks], p[:, 1] - [c.y for c in clicks])
        return np.where(np.isfinite(e), e, np.inf)

    def solve(self, clicks: list[Click], penalty: float, now: float) -> list:
        """Eye weights fitted to ``clicks``, pulled toward the calibration's by ``penalty``."""
        heads = unpack(np.array([c.X for c in clicks]))
        targets = np.array([[c.x, c.y] for c in clicks])
        wanted = self.estimator.model.required(heads, targets)  # (n, 2 eyes, 2)
        F = np.array([h.features for h in heads])  # (n, 2, k)
        age = now - np.array([c.t for c in clicks])
        weight = 0.5 ** (np.maximum(age, 0.0) / HALF_LIFE_S)
        eyes = self._weighted(F, wanted, weight, penalty)
        for _ in range(2):  # Huber: clicks still far off after a fit count less in the next
            e = self.errors(clicks, eyes)
            finite = e[np.isfinite(e)]
            if not len(finite):
                break
            c = 1.5 * max(float(np.median(finite)), 1.0)
            robust = np.where(np.isfinite(e), np.minimum(1.0, c / np.maximum(e, 1e-9)), 0.0)
            eyes = self._weighted(F, wanted, weight * robust, penalty)
        return eyes

    def _weighted(self, F: np.ndarray, wanted: np.ndarray, weight: np.ndarray, penalty: float) -> list:
        eyes = []
        for e, (mean, sd, W0) in enumerate(self.prior):
            Z = np.hstack([np.ones((len(F), 1)), (F[:, e] - mean) / sd])
            P = penalty * np.diag([INTERCEPT_PENALTY] + [1.0] * (Z.shape[1] - 1))
            A = Z.T @ (weight[:, None] * Z) + P
            B = Z.T @ (weight[:, None] * wanted[:, e]) + P @ W0
            eyes.append((mean, sd, np.linalg.solve(A, B)))
        return eyes

    def refit(self, now: float | None = None) -> dict | None:
        """Fit candidates on the older clicks, judge them on the newest, and take the best if it
        beats the model in use and the calibration. Returns the fit's numbers (None: too few)."""
        self.since_fit = 0
        self._gate()
        clicks = sorted(self.usable(), key=lambda c: c.t)
        unseen = [c for c in clicks if c.t > self.learned_until]
        if len(clicks) < self.min_clicks or len(unseen) < MIN_HELD_OUT:
            return None
        now = now if now is not None else clicks[-1].t
        test = unseen[-max(MIN_HELD_OUT, int(HOLDOUT * len(clicks))) :]
        train = clicks[: len(clicks) - len(test)]
        if len(train) < MIN_HELD_OUT:
            return None
        current = float(np.median(self.errors(test, self.estimator.model.eyes)))
        calibration = float(np.median(self.errors(test, self.prior)))
        best, best_error = None, math.inf
        for penalty in PENALTIES:
            error = float(np.median(self.errors(test, self.solve(train, penalty, now))))
            if error < best_error:
                best, best_error = penalty, error
        labels = np.array([[c.x, c.y] for c in test])
        guess = float(np.median(np.hypot(*(labels - np.median(labels, axis=0)).T)))
        accepted = (
            best is not None and best_error < MARGIN * min(current, calibration) and best_error < GUESS_SHARE * guess
        )
        self.fit = {
            "clicks": len(clicks), "penalty": best, "accepted": accepted,
            "error_px": {"calibration": round(calibration, 1), "in_use": round(current, 1),
                         "learned": round(best_error, 1), "fixed_guess": round(guess, 1)},
        }  # fmt: skip
        if accepted:
            self.estimator.model.eyes = self.solve(clicks, best, now)
            self.learned_until = clicks[-1].t
            self.version += 1
            self.updated = datetime.fromtimestamp(now, UTC).isoformat()
            self._save_model()
        return self.fit

    # -- reporting and files ---------------------------------------------------------------------------

    def state(self) -> dict:
        clicks = self.snapshot()
        recent = [c.error_px for c in clicks[-50:] if math.isfinite(c.error_px)]
        return {
            "version": self.version,
            "clicks": len(clicks),
            "skipped": dict(self.skipped),
            "recent_error_px": round(float(np.median(recent)), 1) if recent else None,
            "fit": self.fit,
            "updated": self.updated,
        }

    def _save_click(self, click: Click) -> None:
        if self.folder is None:
            return
        try:
            with (self.folder / CLICKS_FILE).open("a", encoding="utf-8") as out:
                out.write(json.dumps(click.to_json()) + "\n")
        except OSError:
            pass

    def _save_model(self) -> None:
        if self.folder is None:
            return
        data = {
            "calibration": self.calibration, "version": self.version, "t": self.updated, "fit": self.fit,
            "learned_until": self.learned_until,
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
        """Clicks since this calibration, and what was learned from them for it."""
        if self.folder is None:
            return
        path = self.folder / CLICKS_FILE
        if path.is_file():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines:
                try:
                    click = Click.from_json(json.loads(line))
                except (ValueError, KeyError, TypeError):
                    continue
                if (since is None or click.t >= since) and click.X.shape == self.clicks_shape():
                    self.clicks.append(click)
            self.clicks = self.clicks[-MAX_CLICKS:]
            if len(lines) > 2 * MAX_CLICKS:  # keep the file to what is used
                path.write_text("".join(json.dumps(c.to_json()) + "\n" for c in self.clicks), encoding="utf-8")
        learned = self.folder / LEARNED_FILE
        try:
            data = json.loads(learned.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("calibration") != self.calibration or len(data.get("eyes", [])) != len(self.prior):
            return  # learned for another calibration
        eyes = [(np.array(e["mean"]), np.array(e["sd"]), np.array(e["weights"])) for e in data["eyes"]]
        if any(w.shape != p[2].shape for (_, _, w), p in zip(eyes, self.prior, strict=True)):
            return
        self.estimator.model.eyes = eyes
        self.version, self.updated, self.fit = int(data.get("version", 1)), data.get("t"), data.get("fit")
        self.learned_until = float(data.get("learned_until", -math.inf))

    def clicks_shape(self) -> tuple[int]:
        mean = self.prior[0][0]
        return (18 + 2 * len(mean),)


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None

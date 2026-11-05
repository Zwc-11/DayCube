"""Why the gaze slides during a stare: measured from ordinary use.

Eyes at rest do not glide; between saccades they hold still to within a fraction of a degree. So
when the estimated gaze slides slowly while the eyes rest on one point, the estimate is drifting,
and what drifts with it says why. Each frame's vector holds the head's pose and each eye's
measurements (``gaze.head.pack``): the iris's direction in the head and the upper and lower lids'
heights. Over every long stare (the ring filter holding one fixation for ``min_s`` or more without
moving across, so not reading) this fits a straight line through time to:

- the gaze's height on the screen (px/s, positive downward);
- the irises' vertical direction in the head (per second; the iris landmarks sit lower in the eye
  as the upper lid covers more of the iris);
- the lid gap (mm/s; lids relax and close slightly over a long stare);
- the head's pitch (degrees/s) and its distance from the camera (mm/s).

The tracker reports the medians over the last stares (``Tracker.state()["drift"]``) and ``doctor``
names the measurement that moves with the gaze, which says what to fix: a lid or iris drift points
at the eye measurements, a pitch or distance drift at the head.

Each stare also goes to the learner (``on_stare``): its mean vector early in it (from ``EARLY``)
and over its last ``LATE_S``, a pair the gaze model must give the same point for
(``gaze.learn.Stare``), so learning removes whatever drifts there (``eval/stare_learning.py``).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable

import numpy as np

KEEP = 30  # stares remembered
ACROSS = 0.06  # a stare moves less than this share of the screen width across (else: reading)
EARLY = (0.3, 0.8)  # seconds into a stare: its start, for the learner (the ring has settled)
LATE_S = 0.5  # its last half second
MAX_FRAMES = 900  # a longer look is cut into stares of this many frames (30 s at 30 frames/s)


def _slope(t: np.ndarray, v: np.ndarray) -> float:
    t = t - t.mean()
    d = float(t @ t)
    return float(t @ (v - v.mean()) / d) if d > 0 else math.nan


def _pitch(R: np.ndarray) -> np.ndarray:
    """Head pitch (degrees) of each rotation (n, 3, 3)."""
    return np.degrees(np.arctan2(R[:, 2, 1], R[:, 2, 2]))


class DriftProbe:
    """Collects the frames of the fixation the ring filter holds; when it ends, a long stare
    becomes one set of slopes."""

    def __init__(self, width: float, min_s: float = 2.0, on_stare: Callable[..., object] | None = None) -> None:
        self.width, self.min_s, self.on_stare = width, min_s, on_stare
        self.key: float | None = None  # when the current fixation began (Stabilizer.since)
        self.frames: list[tuple[float, float, float, np.ndarray]] = []  # (t, x px, y px, vector)
        self.stares: deque[dict] = deque(maxlen=KEEP)

    def add(self, t: float, fixation: float, x: float, y: float, vector: np.ndarray) -> None:
        if fixation != self.key or len(self.frames) >= MAX_FRAMES:
            self._finish()
            self.key, self.frames = fixation, []
        self.frames.append((t, x, y, vector))

    def _finish(self) -> None:
        frames = self.frames
        if len(frames) < 20 or frames[-1][0] - frames[0][0] < self.min_s:
            return
        xs = np.array([f[1] for f in frames])
        if np.percentile(xs, 90) - np.percentile(xs, 10) > ACROSS * self.width:
            return  # moving across: reading, not a stare
        t = np.array([f[0] for f in frames])
        V = np.array([f[3] for f in frames])
        if V.shape[1] < 26:
            return  # not the head-aware model's vector
        features = V[:, 18:26].reshape(-1, 2, 4)  # per eye: iris x, iris y, upper lid, lower lid
        if self.on_stare is not None:
            into = t - t[0]
            early, late = (into >= EARLY[0]) & (into < EARLY[1]), into >= into[-1] - LATE_S
            if early.sum() >= 5 and late.sum() >= 5:
                self.on_stare(float(t[0]), V[early].mean(0), V[late].mean(0), float(into[-1]))
        self.stares.append({
            "seconds": float(t[-1] - t[0]),
            "gaze_px_s": _slope(t, np.array([f[2] for f in frames])),
            "iris_per_s": _slope(t, features[:, :, 1].mean(1)),
            "lids_mm_s": _slope(t, np.abs(features[:, :, 3] - features[:, :, 2]).mean(1)),
            "pitch_deg_s": _slope(t, _pitch(V[:, :9].reshape(-1, 3, 3))),
            "distance_mm_s": _slope(t, V[:, 11]),
        })  # fmt: skip

    def state(self) -> dict | None:
        if not self.stares:
            return None
        rows = list(self.stares)
        out = {"stares": len(rows), "seconds": round(sum(r["seconds"] for r in rows), 1)}
        for key, digits in (("gaze_px_s", 1), ("iris_per_s", 5), ("lids_mm_s", 4), ("pitch_deg_s", 3),
                            ("distance_mm_s", 2)):  # fmt: skip
            values = [r[key] for r in rows if math.isfinite(r[key])]
            out[key] = round(float(np.median(values)), digits) if values else None
        return out

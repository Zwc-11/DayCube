"""Smoothing and fixation detection for webcam gaze, in plain Python.

- One Euro filter (Casiez, Roussel and Vogel, CHI 2012): smooths heavily while the gaze point is
  still and lightly while it moves, so fixations steady without lagging saccades. The paper's
  mouse settings (1 Hz, beta 0.007) barely smooth webcam noise of 40 px a frame at 15 fps, since
  the noise itself reads as speed; 0.5 Hz and 0.002 halve it and still follow a saccade.
- I-DT (Salvucci and Goldberg, ETRA 2000): a fixation is a run of samples whose dispersion,
  (max x - min x) + (max y - min y), stays under a threshold for at least a minimum duration.

Webcam gaze is noisy (errors of 100-200 px are typical), so the dispersion threshold starts at 6%
of the screen width (115 px at 1920) rather than the 25-50 px used with infrared trackers, and
rises with the noise actually measured: ``noise_k`` times a robust estimate of the smoothed gaze's
scatter over the last few seconds. Without that, a session noisier than the fixed threshold
assumes (a camera at 640x480, dim light, a stale calibration) breaks a steady look into pieces
shorter than ``min_duration``, which are dropped: on the 2026-10-23 Windows session the tracker
turned about a third of tracked time into fixations (``eval/gaze_sim.py``). Adapting the threshold
to measured noise follows Nystrom and Holmqvist (Behavior Research Methods, 2010), who set the
velocity threshold of their detector from the noise of each recording.

The scatter is estimated from second differences at a lag longer than the smoother's memory,
``x[t] - 2 x[t-L] + x[t-2L]``: noise survives them and steady motion (a slow drift, a pursuit)
does not. Saccades inflate the differences that span one, so the estimate comes from their lower
quartile, and differences that span a gap in the gaze are left out. A threshold never exceeds
``max_dispersion`` of the screen width (15%), so looks at distant targets stay apart however noisy
the gaze.

Fixations longer than ``max_duration`` are split, so a long stare is reported while it lasts
instead of only when it ends.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Fixation:
    start: float  # epoch seconds
    end: float
    x: float  # fractions of the monitor
    y: float
    samples: int


class OneEuro:
    def __init__(self, min_cutoff: float = 0.5, beta: float = 0.002, d_cutoff: float = 1.0) -> None:
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self) -> None:
        self.t: float | None = None
        self.value = 0.0
        self.speed = 0.0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, t: float, value: float) -> float:
        if self.t is None or t <= self.t:
            self.t, self.value, self.speed = t, value, 0.0
            return value
        dt = t - self.t
        speed = (value - self.value) / dt
        self.speed += self._alpha(self.d_cutoff, dt) * (speed - self.speed)
        cutoff = self.min_cutoff + self.beta * abs(self.speed)
        self.value += self._alpha(cutoff, dt) * (value - self.value)
        self.t = t
        return self.value


class Smoother:
    """One Euro on each axis, in pixels (the filter's ``beta`` is tuned for pixel speeds)."""

    def __init__(self, min_cutoff: float = 0.5, beta: float = 0.002) -> None:
        self.x, self.y = OneEuro(min_cutoff, beta), OneEuro(min_cutoff, beta)

    def __call__(self, t: float, x: float, y: float) -> tuple[float, float]:
        return self.x(t, x), self.y(t, y)

    def reset(self) -> None:
        self.x.reset()
        self.y.reset()


class FixationDetector:
    def __init__(
        self,
        width: int,
        height: int,
        *,
        dispersion_px: float | None = None,
        min_duration: float = 0.1,
        max_duration: float = 1.0,
        max_gap: float = 0.25,
        noise_k: float = 6.0,
        noise_window: float = 8.0,
        noise_lag: float = 0.3,
        max_dispersion: float = 0.15,
    ) -> None:
        self.width, self.height = width, height
        self.base = (dispersion_px if dispersion_px is not None else 0.06 * width) / width  # in x-fractions
        self.dispersion = self.base
        self.min_duration, self.max_duration, self.max_gap = min_duration, max_duration, max_gap
        self.noise_k, self.noise_window, self.noise_lag = noise_k, noise_window, noise_lag
        self.max_dispersion = max(max_dispersion, self.base)
        self.noise: float | None = None  # scatter of the smoothed gaze, in x-fractions
        self.history: deque[tuple[float, float, float]] = deque()  # recent samples for the noise estimate
        self.since_estimate = 0
        self.window: list[tuple[float, float, float]] = []

    @property
    def noise_px(self) -> float | None:
        return None if self.noise is None else self.noise * self.width

    def _spread(self, window: list[tuple[float, float, float]]) -> float:
        xs = [x for _, x, _ in window]
        ys = [y * self.height / self.width for _, _, y in window]  # y in x-fraction units, so pixels weigh equally
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    def _emit(self, window: list[tuple[float, float, float]]) -> list[Fixation]:
        if len(window) < 2 or window[-1][0] - window[0][0] < self.min_duration:
            return []
        n = len(window)
        return [Fixation(window[0][0], window[-1][0], sum(w[1] for w in window) / n, sum(w[2] for w in window) / n, n)]

    def _learn(self, t: float, x: float, y: float) -> None:
        """Keep the noise estimate, and the threshold that follows it, up to date."""
        history = self.history
        history.append((t, x, y * self.height / self.width))
        while history and t - history[0][0] > self.noise_window:
            history.popleft()
        self.since_estimate += 1
        if self.since_estimate < 10:
            return
        self.since_estimate = 0
        samples = list(history)
        times = [s[0] for s in samples]
        diffs: list[float] = []
        lag, j, k = self.noise_lag, 0, 0
        for i, (ti, xi, yi) in enumerate(samples):  # i is the newest of the three points
            while j < i and times[j + 1] <= ti - lag:
                j += 1
            if not lag <= ti - times[j] <= lag + self.max_gap:  # a gap in between (blink, camera off)
                continue
            tj = times[j]
            while k < j and times[k + 1] <= tj - lag:
                k += 1
            if not lag <= tj - times[k] <= lag + self.max_gap:
                continue
            (_, xj, yj), (_, xk, yk) = samples[j], samples[k]
            diffs += (abs(xi - 2 * xj + xk), abs(yi - 2 * yj + yk))
        if len(diffs) < 40:
            return
        diffs.sort()
        # For independent noise a second difference has variance 6 sigma^2. The lower quartile of
        # its size is 0.3186 of its spread, and is left alone by the saccades that make the larger
        # differences (with short fixations most differences span one), where a median is not.
        self.noise = diffs[len(diffs) // 4] / (0.3186 * math.sqrt(6))
        self.dispersion = min(max(self.base, self.noise_k * self.noise), self.max_dispersion)

    def add(self, t: float, x: float, y: float) -> list[Fixation]:
        """Add one gaze sample (monitor fractions); returns fixations that it completed."""
        out: list[Fixation] = []
        if self.window and t - self.window[-1][0] > self.max_gap:  # a blink or a lost face
            out += self.flush()
        self._learn(t, x, y)
        self.window.append((t, x, y))
        if self._spread(self.window) > self.dispersion:
            sample = self.window.pop()
            if self.window and self.window[-1][0] - self.window[0][0] >= self.min_duration:
                out += self._emit(self.window)
                self.window = [sample]
            else:  # too short to be a fixation: slide the window forward
                self.window.append(sample)
                while len(self.window) > 1 and self._spread(self.window) > self.dispersion:
                    self.window.pop(0)
        elif self.window[-1][0] - self.window[0][0] >= self.max_duration:
            out += self._emit(self.window)
            self.window = []
        return out

    def flush(self) -> list[Fixation]:
        out, self.window = self._emit(self.window), []
        return out

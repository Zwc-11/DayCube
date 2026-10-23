"""Smoothing and fixation detection for webcam gaze, in plain Python.

- One Euro filter (Casiez, Roussel and Vogel, CHI 2012): smooths heavily while the gaze point is
  still and lightly while it moves, so fixations steady without lagging saccades. The paper's
  mouse settings (1 Hz, beta 0.007) barely smooth webcam noise of 40 px a frame at 15 fps, since
  the noise itself reads as speed; 0.5 Hz and 0.002 halve it and still follow a saccade.
- I-DT (Salvucci and Goldberg, ETRA 2000): a fixation is a run of samples whose dispersion,
  (max x - min x) + (max y - min y), stays under a threshold for at least a minimum duration.

Webcam gaze is noisy (errors of 100-200 px are typical), so the default dispersion threshold is
6% of the screen width (115 px at 1920) rather than the 25-50 px used with infrared trackers.
Fixations longer than ``max_duration`` are split, so a long stare is reported while it lasts
instead of only when it ends.
"""

from __future__ import annotations

import math
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
    ) -> None:
        self.width, self.height = width, height
        self.dispersion = (dispersion_px if dispersion_px is not None else 0.06 * width) / width  # in x-fractions
        self.min_duration, self.max_duration, self.max_gap = min_duration, max_duration, max_gap
        self.window: list[tuple[float, float, float]] = []

    def _spread(self, window: list[tuple[float, float, float]]) -> float:
        xs = [x for _, x, _ in window]
        ys = [y * self.height / self.width for _, _, y in window]  # y in x-fraction units, so pixels weigh equally
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    def _emit(self, window: list[tuple[float, float, float]]) -> list[Fixation]:
        if len(window) < 2 or window[-1][0] - window[0][0] < self.min_duration:
            return []
        n = len(window)
        return [Fixation(window[0][0], window[-1][0], sum(w[1] for w in window) / n, sum(w[2] for w in window) / n, n)]

    def add(self, t: float, x: float, y: float) -> list[Fixation]:
        """Add one gaze sample (monitor fractions); returns fixations that it completed."""
        out: list[Fixation] = []
        if self.window and t - self.window[-1][0] > self.max_gap:  # a blink or a lost face
            out += self.flush()
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

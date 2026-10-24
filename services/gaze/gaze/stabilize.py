"""The gaze ring's own filter: still while the eyes rest, quick when they jump.

What the ring should show is where the eyes are looking now, and the eyes do two things: they rest
(fixations, 200-400 ms when reading, longer on a picture) and they jump (saccades, 30-80 ms).
Webcam gaze adds tens of pixels of jitter to every frame, correlated over a few frames, plus the
odd frame with a bad landmark far off. A filter that treats every sample alike must trade the two:
smooth enough to hold still in a fixation and it lags behind every saccade, quick enough to follow
saccades and it shakes. The One Euro filter the ring used (Casiez et al., CHI 2012) opens up with
speed, but at webcam noise the jitter itself reads as speed, so the ring shook: the person called
it "too sensitive". Feit et al. (CHI 2017, "Toward Everyday Gaze Input", 80 people on two
trackers) found the same: One Euro's speed adaptation "is also adjusted to noise artifacts that
show similar dynamics as saccades", and the best filters for a gaze cursor were a weighted
average within a fixation with saccade detection, and outlier correction. Spakov (ETRA 2012) came
to the same conclusion over many filters: the key is telling the signal's state (resting or
jumping) apart. Tobii's "zero delay" gaze filter (US11915521B2) averages every sample since the
fixation began and does not filter saccades at all.

So the ring's filter keeps a fixation estimate and moves it in three ways:

- **Resting.** Each sample near the estimate pulls it by ``max(1/n, 1 - exp(-dt/tau_s))``: the plain
  mean of the fixation's first samples (the noise falls with their number), then an exponential
  average over ``tau_s``, so a slow drift (reading along a line) is followed smoothly.
- **A far jump.** A sample farther than ``k`` times the noise from the estimate is held back: a
  single one is an outlier (a bad frame) and is ignored. ``confirm`` far samples in a row that
  agree with each other are a saccade: the estimate jumps to them.
- **A near jump** (the next button, the next line) is within the noise of one sample, but not of
  several: the median of the last 4, 6 or 8 samples farther from the estimate than ``k_window``
  times what such a median usually is moves the estimate there, if those samples are one look
  (all but one within the far threshold of their median). The median ignores the odd bad frame;
  measured per window length, the threshold follows the noise's own correlation.

The noise is measured as it goes: the median distance of samples from the estimate (and of each
window's median), tracked with multiplicative steps over ``scale_s``, which needs no history and
is not moved by saccades (a far sample only says "above the median"). A sharper calibration gets a
tighter threshold and a quicker ring. The far-jump threshold is kept within ``min_jump`` and
``max_jump`` of the screen width.

``eval/ring_filter.py`` measures it against the One Euro ring on simulated webcam gaze (the PC's
error model, 30 and 60 frames/s): at 30 frames/s it halves the shake and spread of the resting
ring at 20 ms more latency on far jumps (eval/results/ring-filter.json has the numbers).
"""

from __future__ import annotations

import math
from collections import deque

WINDOWS = (4, 6, 8)  # samples in the near-jump tests


def _median(values: list[float]) -> float:
    values.sort()
    n = len(values)
    return values[n // 2] if n % 2 else 0.5 * (values[n // 2 - 1] + values[n // 2])


class Stabilizer:
    """Display filter for the gaze ring, in screen pixels. ``__call__(t, x, y)`` gives the point to
    show for a gaze sample; ``reset`` after a gap (face lost, a long blink)."""

    def __init__(
        self,
        width: float,
        *,
        tau_s: float = 0.2,
        k: float = 4.5,
        confirm: int = 2,
        k_window: float = 3.0,
        windows: tuple[int, ...] = WINDOWS,
        min_jump: float = 0.01,
        max_jump: float = 0.12,
        scale_s: float = 3.0,
    ) -> None:
        self.tau_s, self.k, self.confirm, self.k_window, self.scale_s = tau_s, k, confirm, k_window, scale_s
        self.windows = tuple(sorted(windows))
        self.min_jump, self.max_jump = min_jump * width, max_jump * width
        # The noise: median distance of a sample (and of each window's median) from the estimate.
        self.scale = 0.03 * width
        self.window_scale = dict.fromkeys(self.windows, self.scale)
        self.recent: deque[tuple[float, float]] = deque(maxlen=max(self.windows, default=1))
        self.pending: list[tuple[float, float]] = []  # far samples that may be a saccade
        self.x = self.y = 0.0
        self.n = 0  # samples in the estimate; 0: none yet
        self.t = 0.0
        self.jumps = 0  # how many times the estimate jumped (far or near), for the tracker's report

    def reset(self) -> None:
        self.n = 0
        self.pending = []
        self.recent.clear()

    def resume(self, t: float, interval: float) -> None:
        """Go on after a brief blink as if the last sample came one frame ago: time with the eyes
        shut is not time the estimate was watched, so it must not raise the next sample's pull."""
        if self.n:
            self.t = t - interval

    @property
    def threshold(self) -> float:
        """How far a sample must be from the estimate to be a far one (pixels)."""
        return min(max(self.k * self.scale, self.min_jump), self.max_jump)

    def _jump(self, points: list[tuple[float, float]], x: float, y: float) -> tuple[float, float]:
        self.x, self.y, self.n = x, y, len(points)
        self.pending = []
        self.recent.clear()
        self.recent.extend(points)
        self.jumps += 1
        return x, y

    def __call__(self, t: float, x: float, y: float) -> tuple[float, float]:
        if self.n == 0:
            self.x, self.y, self.n, self.t = x, y, 1, t
            self.recent.append((x, y))
            return x, y
        dt = max(t - self.t, 1e-3)
        self.t = t
        step = min(1.0, dt / self.scale_s)  # the noise trackers' step, a share of their value
        threshold = self.threshold
        distance = math.hypot(x - self.x, y - self.y)
        self.scale *= math.exp(step if distance > self.scale else -step)
        self.recent.append((x, y))
        if distance > threshold:
            if self.pending and math.hypot(x - self.pending[-1][0], y - self.pending[-1][1]) > threshold:
                self.pending = []  # far, but not near the last far one: an outlier, or still moving
            self.pending.append((x, y))
            if len(self.pending) >= self.confirm:
                n = len(self.pending)
                return self._jump(
                    self.pending, sum(p[0] for p in self.pending) / n, sum(p[1] for p in self.pending) / n
                )
        else:
            self.pending = []
            gain = max(1.0 / (self.n + 1), 1.0 - math.exp(-dt / self.tau_s))
            self.x += gain * (x - self.x)
            self.y += gain * (y - self.y)
            self.n += 1
        recent = list(self.recent)
        for w in self.windows:
            if len(recent) < w:
                break
            last = recent[-w:]
            mx, my = _median([p[0] for p in last]), _median([p[1] for p in last])
            gap = math.hypot(mx - self.x, my - self.y)
            scale = self.window_scale[w]
            # The window must be one look: all but one of its samples near its median, not a
            # saccade under way or a bad frame and a jump straddling it.
            if (
                gap > self.k_window * scale
                and sum(math.hypot(p[0] - mx, p[1] - my) <= threshold for p in last) >= w - 1
            ):
                return self._jump(last, mx, my)
            self.window_scale[w] = scale * math.exp(step if gap > scale else -step)
        return self.x, self.y

"""Clicks as moments when the eyes are known: how far off the tracker is now, and where.

People look at what they click. The eyes reach a link or button before the pointer does and stay
on it until the click, so a click with a fixation around it is a calibration point that costs the
user nothing: the difference between where the tracker put the eyes and where the click landed is
the tracker's error at that place and moment. WebGazer (Papoutsaki et al., IJCAI 2016) trains its
gaze model on clicks for the same reason.

Two things come out of the clicks:

- the live error: how far off the tracker is now. The calibration measures this once, with the
  head still; after the head moves it is usually worse, and nothing else notices. It sets the
  spread the attention filter allows (once ``min_live`` clicks agree) and is shown in /status.
- an offset correction: a weighted average of recent residuals, near in time and on screen, shrunk
  toward no correction when there are few (``prior`` is worth that many clicks of zero offset).
  Webcam gaze error is mostly a slowly changing offset (head position), so this removes much of it.

A click far from where the tracker looked (``gate`` times the calibrated spread, at least a tenth
of the screen width) is not used: people sometimes click without looking (a familiar button, the
same place again). Rejections are counted; many of them mean the calibration no longer fits.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass

RAYLEIGH_MEDIAN = math.sqrt(2 * math.log(2))  # median distance of a 2-D Gaussian, in sigmas


@dataclass(frozen=True)
class Anchor:
    t: float  # epoch seconds of the click
    x: float  # the click, monitor pixels
    y: float
    dx: float  # where the tracker put the eyes minus the click, monitor pixels
    dy: float


class Anchors:
    def __init__(
        self,
        *,
        before_s: float = 0.45,
        after_s: float = 0.1,
        wait_s: float = 10.0,  # an anchor dated up to this far after "now" still counts (two clocks)
        keep: int = 40,
        max_age_s: float = 1800.0,
        tau_s: float = 600.0,
        length: float = 0.3,
        prior: float = 1.0,
        gate: float = 4.0,
        min_live: int = 5,
    ) -> None:
        self.before_s, self.after_s, self.wait_s = before_s, after_s, wait_s
        self.max_age_s, self.tau_s, self.length, self.prior = max_age_s, tau_s, length, prior
        self.gate, self.min_live = gate, min_live
        self.monitor: tuple[int, int] | None = None
        self.calibrated_px: float | None = None  # held-out median error from the calibration
        self.recent: deque[tuple[float, float, float, float]] = deque()  # fixations: t, t_end, x px, y px
        self.pending: deque[tuple[float, float, float]] = deque(maxlen=50)  # clicks awaiting gaze: t, x, y fractions
        self.anchors: deque[Anchor] = deque(maxlen=keep)
        self.rejected = 0
        self._live: tuple[int, float | None, float | None] | None = None  # cache: version, raw, corrected
        self._version = 0

    def set_monitor(self, monitor: tuple[int, int] | None, calibrated_px: float | None) -> None:
        """The tracker's monitor and held-out error. Another monitor, or a new calibration (its error
        changes), makes what was learned from clicks wrong for the new gaze model: start over."""
        if monitor is None:
            self.calibrated_px = calibrated_px
            return
        other = self.monitor is not None and monitor != self.monitor
        recalibrated = None not in (self.calibrated_px, calibrated_px) and calibrated_px != self.calibrated_px
        if other or recalibrated:
            self.recent.clear()
            self.pending.clear()
            self.anchors.clear()
            self.rejected = 0
            self._version += 1
        self.monitor, self.calibrated_px = monitor, calibrated_px

    # -- evidence ----------------------------------------------------------------------------------

    def fixation(self, t: float, t_end: float, fx: float, fy: float) -> None:
        """A fixation as the tracker reported it (monitor fractions, before any correction)."""
        if self.monitor is None:
            return
        w, h = self.monitor
        self.recent.append((t, t_end, fx * w, fy * h))
        while self.recent and t_end - self.recent[0][1] > 30:
            self.recent.popleft()
        self._resolve(t_end)

    def click(self, t: float, fx: float, fy: float) -> None:
        """A click on the calibrated monitor, as monitor fractions. It waits for the gaze around it,
        which arrives a second or two later, and for the tracker to say which monitor it is on."""
        self.pending.append((t, fx, fy))  # at most 50 wait: with no tracker running, the oldest go
        if self.recent:
            self._resolve(max(f[1] for f in self.recent))

    def _resolve(self, seen_until: float) -> None:
        """Match clicks with the gaze around them once fixations past them have arrived."""
        if not self.pending or self.monitor is None:
            return
        w, h = self.monitor
        keep: deque[tuple[float, float, float]] = deque()
        for t, cx, cy in self.pending:
            x, y = cx * w, cy * h
            if seen_until < t + self.after_s:
                keep.append((t, cx, cy))
                continue
            lo, hi = t - self.before_s, t + self.after_s
            weight = gx = gy = 0.0
            for start, end, px, py in self.recent:
                overlap = min(end, hi) - max(start, lo)
                if overlap > 0:
                    weight += overlap
                    gx += overlap * px
                    gy += overlap * py
            if weight == 0:
                continue  # no gaze at the click (a blink, eyes on the keyboard)
            dx, dy = gx / weight - x, gy / weight - y
            spread = (self.calibrated_px or 90.0) / RAYLEIGH_MEDIAN
            if math.hypot(dx, dy) > max(self.gate * spread, 0.1 * self.monitor[0]):
                self.rejected += 1
                continue
            self.anchors.append(Anchor(t, x, y, dx, dy))
            self._version += 1
        self.pending = deque(keep, maxlen=self.pending.maxlen)

    # -- what was learned --------------------------------------------------------------------------

    def _usable(self, now: float) -> list[Anchor]:
        return [a for a in self.anchors if -self.wait_s <= now - a.t <= self.max_age_s]

    def _estimate(self, anchors: list[Anchor], x: float, y: float, now: float) -> tuple[float, float]:
        """The offset at a point (monitor px), from these anchors."""
        if not anchors or self.monitor is None:
            return 0.0, 0.0
        scale = 2 * (self.length * self.monitor[0]) ** 2
        num_x = num_y = 0.0
        den = self.prior
        for a in anchors:
            w = math.exp(-max(now - a.t, 0.0) / self.tau_s - ((x - a.x) ** 2 + (y - a.y) ** 2) / scale)
            num_x += w * a.dx
            num_y += w * a.dy
            den += w
        return num_x / den, num_y / den

    def offset(self, fx: float, fy: float, now: float) -> tuple[float, float]:
        """The tracker's estimated error at a point, in monitor fractions (subtract it to correct)."""
        if self.monitor is None or not self.anchors:
            return 0.0, 0.0
        w, h = self.monitor
        dx, dy = self._estimate(self._usable(now), fx * w, fy * h, now)
        return dx / w, dy / h

    def live(self, now: float) -> tuple[int, float | None, float | None]:
        """Clicks used, median error before correction, and median error after it. Each click is
        corrected only from the clicks before it, as the correction is used live, so the figure is
        neither flattered by fitting itself nor by knowing the future."""
        anchors = self._usable(now)
        if self._live is not None and self._live[0] == self._version and len(anchors) == len(self.anchors):
            return len(anchors), self._live[1], self._live[2]
        if not anchors:
            return 0, None, None
        raw = statistics.median(math.hypot(a.dx, a.dy) for a in anchors)
        corrected = None
        if len(anchors) >= 2:
            ordered = sorted(anchors, key=lambda a: a.t)
            errors = []
            for i, a in enumerate(ordered[1:], start=1):
                ox, oy = self._estimate(ordered[:i], a.x, a.y, a.t)
                errors.append(math.hypot(a.dx - ox, a.dy - oy))
            corrected = statistics.median(errors)
        if len(anchors) == len(self.anchors):
            self._live = (self._version, raw, corrected)
        return len(anchors), raw, corrected

    def error_px(self, now: float) -> float | None:
        """The median error to plan for now: from clicks once there are enough and they are recent
        (the correction they support fades over ``tau_s``), else None."""
        n, _, corrected = self.live(now)
        if n < self.min_live or now - max(a.t for a in self.anchors) > self.tau_s:
            return None
        return corrected

    def health(self, now: float) -> dict:
        n, raw, corrected = self.live(now)
        offset = None
        if self.monitor is not None and n:
            w, h = self.monitor
            dx, dy = self._estimate(self._usable(now), w / 2, h / 2, now)
            offset = [round(dx, 1), round(dy, 1)]
        return {
            "clicks": n,
            "rejected": self.rejected,
            "live_error_px": None if raw is None else round(raw, 1),
            "corrected_error_px": None if corrected is None else round(corrected, 1),
            "offset_px": offset,
        }

"""How still is the gaze ring while the eyes rest, and how quickly does it follow a jump?

Simulated webcam gaze (the error model of eval/gaze_sim.py, fitted to the PC's calibration: 85 px
median error, 170 px 90th percentile; per frame that is 49 px of jitter correlated over ~65 ms, 8%
of frames with a landmark failure of ~200 px, a slow head drift and the calibration's bias) for five
kinds of looking:

- prose: lines of 9 fixations of 180-280 ms, 60-120 px apart, then a return sweep to the next line;
- code: 8-14 short indented lines of 2-5 fixations of 250-600 ms, now and then a line back up;
- looking around: fixations of 300-900 ms at random points, saccades of 300-1,800 px;
- nearby: fixations of 250-700 ms on points 120-300 px apart (a toolbar, a menu, a form);
- stares: one point for 3-8 s.

Saccades take 25-70 ms. The tracker gets one gaze sample per camera frame; the ring is drawn at the
display's rate (240 Hz here) the way the overlay's ``Trail`` draws it: the filter's output one
sample interval ago, linearly between the samples around that moment.

Measured at every display frame:

- shake: RMS movement of the ring between consecutive display frames while the eyes rest (from
  100 ms into a fixation): what reads as "too sensitive";
- spread: RMS distance of the ring from its own mean within each fixation;
- jump latency: for saccades of 300 px or more, the time from the start of the saccade until the
  ring has covered 70% of it (median and 90th percentile), and for those of 120-300 px (near);
- on the line: in blocks of prose and of code (short indented lines, long fixations, now and
  then a line back up), the share of the time the ring's height is within half a line (18 px) of
  the eyes', each block's mean offset (the calibration's bias there) taken out; and for code, how
  far the ring moves down per line the eyes move down (1: a line per line);
- stare drift: over looks of 3 s or more, how far the ring's height moved from 0.5 s in to the
  end; the second set of runs adds a gaze estimate sliding down 20 px/s while the eyes rest (a
  drooping lid, ``gaze/drift.py``);
- pursuit lag: how far behind the ring falls while the eyes follow text scrolling at 150 px/s;
- error: median distance of the ring from where the eyes are (includes the calibration's bias, the
  same for every filter).

    services/gaze/.venv/bin/python eval/ring_filter.py --out eval/results/ring-filter.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/gaze"))
sys.path.insert(0, str(ROOT / "eval"))

from gaze.fixations import Smoother  # noqa: E402
from gaze.overlay import MAX_DELAY_S, MIN_DELAY_S  # noqa: E402
from gaze.stabilize import Stabilizer  # noqa: E402
from gaze_sim import Camera, ErrorModel  # noqa: E402

MONITOR = (2560, 1440)
DISPLAY_HZ = 240.0
Filter = Callable[[float, float, float], tuple[float, float]]


STARE, PROSE, CODE = 1, 2, 3  # kinds of look (column 4 of the script); 0: anything else
PITCH = 36  # px between lines of text (the user's body text: 28.8 CSS px at 125%)


def script(rng: random.Random, seconds: float) -> np.ndarray:
    """Looks as rows of (start, end, x, y, kind, block); saccades are the gaps between them."""
    looks: list[tuple[float, float, float, float, int, int]] = []
    t, block = 0.0, 0

    def look(d: float, x: float, y: float, kind: int = 0) -> None:
        nonlocal t
        looks.append((t, t + d, x, y, kind, block))
        t += d + rng.uniform(0.025, 0.045)

    while t < seconds:
        block += 1
        u = rng.random()
        if u < 0.12:  # a stare: one point for 3-8 s
            look(rng.uniform(3.0, 8.0), rng.uniform(300, MONITOR[0] - 300), rng.uniform(200, MONITOR[1] - 400), STARE)
        elif u < 0.37:  # a paragraph of prose: lines of 9 fixations, a return sweep to the next line
            x0, y = rng.uniform(300, 900), rng.uniform(200, 1100)
            for _ in range(5):
                x = x0
                for _ in range(9):
                    look(rng.uniform(0.18, 0.28), x, y + rng.gauss(0, 4), PROSE)
                    x += rng.uniform(60, 120)
                y += PITCH
        elif u < 0.62:  # code: short indented lines, long fixations, now and then a line back up
            x0, y = rng.uniform(200, 1200), rng.uniform(200, 900)
            for _ in range(rng.randint(8, 14)):
                x = x0 + 32 * rng.randint(0, 3)
                for _ in range(rng.randint(2, 5)):
                    look(rng.uniform(0.25, 0.6), x, y + rng.gauss(0, 3), CODE)
                    x += rng.uniform(50, 150)
                u = rng.random()
                y += -PITCH if u < 0.1 else 2 * PITCH if u < 0.25 else PITCH
        elif u < 0.82:  # looking around
            for _ in range(8):
                look(rng.uniform(0.3, 0.9), rng.uniform(100, MONITOR[0] - 100), rng.uniform(100, MONITOR[1] - 100))
        else:  # nearby targets (a toolbar, a menu, a form)
            x, y = rng.uniform(400, MONITOR[0] - 400), rng.uniform(400, MONITOR[1] - 400)
            for _ in range(8):
                look(rng.uniform(0.25, 0.7), x, y)
                angle, step = rng.uniform(0, 2 * math.pi), rng.uniform(120, 300)
                x = min(max(x + step * math.cos(angle), 100), MONITOR[0] - 100)
                y = min(max(y + step * math.sin(angle), 100), MONITOR[1] - 100)
    return np.array(looks)


def truth(looks: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Where the eyes are at each time (moving linearly through a saccade), whether they rest, the
    look they rest on (or have just left), and the time since that look began."""
    i = np.clip(np.searchsorted(looks[:, 0], t, side="right") - 1, 0, len(looks) - 1)
    j = np.minimum(i + 1, len(looks) - 1)
    end = looks[i, 1]
    rest = (t <= end) | (j == i)
    k = np.where(rest, 0.0, (t - end) / np.maximum(looks[j, 0] - end, 1e-9))
    xy = looks[i, 2:4] + (looks[j, 2:4] - looks[i, 2:4]) * k[:, None]
    return xy, rest, i, t - looks[i, 0]


def run(make: Callable[[], Filter], seed: int, fps: float, seconds: float = 120.0, drift_px_s: float = 0.0) -> dict:
    """One simulated session through the filter ``make()`` builds; ``drift_px_s``: the estimate
    sliding down while the eyes rest (from where each look began), as a drooping lid would make it."""
    rng = random.Random(seed)
    looks = script(rng, seconds)
    camera = Camera(ErrorModel.fit(85.0, 170.0), random.Random(seed + 1), MONITOR)
    flt = make()
    ts = np.arange(0.0, seconds, 1.0 / fps)
    eyes, resting, which, since = truth(looks, ts)
    out = np.empty((len(ts), 2))
    spent = 0.0
    for n, (t, (tx, ty)) in enumerate(zip(ts.tolist(), eyes.tolist(), strict=True)):
        rx, ry = camera.predict(t, (tx, ty))
        if drift_px_s and resting[n]:
            ry += drift_px_s * since[n]
        begin = time.perf_counter()
        out[n] = flt(t, rx, ry)
        spent += time.perf_counter() - begin
    # The ring as the overlay's Trail draws it: the output one sample interval ago, interpolated.
    delay = min(MAX_DELAY_S, max(MIN_DELAY_S, 1.0 / fps))
    shown_t = np.arange(ts[0] + delay, seconds, 1.0 / DISPLAY_HZ)
    ring = np.column_stack([np.interp(shown_t - delay, ts, out[:, a]) for a in range(2)])
    eye, rest, look, into = truth(looks, shown_t)

    settled = rest & (into >= 0.1)
    steps = np.hypot(*np.diff(ring, axis=0).T)
    shake = float(np.sqrt(np.mean(steps[settled[1:] & settled[:-1]] ** 2)))
    spreads = []
    for i in np.unique(look[settled]):
        pts = ring[settled & (look == i)]
        if len(pts) >= 5:
            spreads.append(np.mean(np.sum((pts - pts.mean(0)) ** 2, axis=1)))
    latencies: dict[bool, list[float]] = {True: [], False: []}  # far jump: seconds to 70% of it
    for i in range(len(looks) - 1):
        jump = looks[i + 1, 2:4] - looks[i, 2:4]
        size = float(np.hypot(*jump))
        before = (look == i) & settled
        left = looks[i, 1]
        window = (shown_t >= left) & (shown_t <= left + 1.0)
        if size < 120 or before.sum() < 3 or window.sum() < 3:
            continue
        origin = ring[before][-5:].mean(0)
        reached = np.flatnonzero((ring[window] - origin) @ jump / (size * size) >= 0.7)
        latencies[size >= 300].append(float(shown_t[window][reached[0]] - left) if len(reached) else 1.0)
    far, near = np.array(latencies[True]) * 1000, np.array(latencies[False]) * 1000
    lines = {kind: _lines(looks, kind, ring, eye, rest, look, into) for kind in (PROSE, CODE)}
    drifts = []  # long stares: how far the ring moved vertically from 0.5 s in to the end
    for i in np.flatnonzero(looks[:, 1] - looks[:, 0] >= 3.0):
        early = (look == i) & (into >= 0.5) & (into < 0.7)
        late = (look == i) & rest & (into >= looks[i, 1] - looks[i, 0] - 0.2)
        if early.any() and late.any():
            drifts.append(abs(float(ring[late, 1].mean() - ring[early, 1].mean())))
    return {
        "shake_px": shake,
        "spread_px": float(np.sqrt(np.mean(spreads))),
        "jump_latency_ms": float(np.median(far)),
        "jump_latency_p90_ms": float(np.percentile(far, 90)),
        "near_jump_latency_ms": float(np.median(near)),
        "stare_drift_px": float(np.median(drifts)) if drifts else math.nan,
        "prose_on_line": lines[PROSE][0],
        "code_on_line": lines[CODE][0],
        "code_line_gain": lines[CODE][1],
        "error_px": float(np.median(np.hypot(*(ring - eye).T)[rest])),
        "us_per_sample": spent / len(ts) * 1e6,
    }


def _lines(
    looks: np.ndarray,
    kind: int,
    ring: np.ndarray,
    eye: np.ndarray,
    rest: np.ndarray,
    look: np.ndarray,
    into: np.ndarray,
) -> tuple[float, float]:
    """How well the ring keeps to the line being read, in the blocks of text of ``kind``: the share
    of the time (from 150 ms into each fixation) its height is within half a line of the eyes',
    and how far it moves down per line the eyes move down (1: one line per line). Each block's
    mean offset (the calibration's bias there) is taken out first: that is for the click learning
    and the line snapping to fix, the same for every filter."""
    near = []
    sxy = sxx = 0.0
    for b in np.unique(looks[looks[:, 4] == kind, 5]):
        mask = rest & (into >= 0.15) & np.isin(look, np.flatnonzero((looks[:, 5] == b) & (looks[:, 4] == kind)))
        if mask.sum() < 30:
            continue
        ry, ey = ring[mask, 1] - ring[mask, 1].mean(), eye[mask, 1] - eye[mask, 1].mean()
        near.append(np.abs(ry - ey) < PITCH / 2)
        sxy, sxx = sxy + float(ry @ ey), sxx + float(ey @ ey)
    if not near:
        return math.nan, math.nan
    return float(np.concatenate(near).mean()), sxy / sxx if sxx else math.nan


def pursuit(make: Callable[[], Filter], seed: int, fps: float, speed: float = 150.0) -> float:
    """How far behind the ring falls (px) while the eyes follow text scrolling up at ``speed``
    px/s for 2 s (a touchpad's smooth scroll), from 0.5 s in, against the same eyes resting."""
    rng = random.Random(seed)
    camera = Camera(ErrorModel.fit(85.0, 170.0), random.Random(seed + 1), MONITOR)
    flt = make()
    lags = []
    t0 = 0.0
    for _ in range(20):
        x, y = rng.uniform(400, MONITOR[0] - 400), rng.uniform(400, MONITOR[1] - 700)
        offsets = {False: [], True: []}
        for n in range(int(4.0 * fps)):
            t = n / fps
            moving = t >= 2.0
            ey = y + (speed * (t - 2.0) if moving else 0.0)
            rx, ry = camera.predict(t0 + t, (x, ey))
            out = flt(t0 + t, rx, ry)
            if t % 2.0 >= 0.5:
                offsets[moving].append(ey - out[1])
        lags.append(float(np.median(offsets[True]) - np.median(offsets[False])))
        t0 += 4.0 + rng.uniform(0.03, 0.07)
    return float(np.median(lags))


def summary(make: Callable[[], Filter], fps: float, seeds: int, drift_px_s: float = 0.0) -> dict:
    runs = [run(make, 1000 + s, fps, drift_px_s=drift_px_s) for s in range(seeds)]
    for s, r in enumerate(runs):
        r["pursuit_lag_px"] = pursuit(make, 2000 + s, fps)
    digits = {"prose_on_line": 3, "code_on_line": 3, "code_line_gain": 2}
    return {k: round(float(np.nanmedian([r[k] for r in runs])), digits.get(k, 1)) for k in runs[0]}


METHODS: dict[str, Callable[[], Filter]] = {
    "one euro (first)": Smoother,
    "stabilizer (before)": lambda: Stabilizer(MONITOR[0], tau_y_s=None),
    "stabilizer": lambda: Stabilizer(MONITOR[0]),
}
DRIFT_PX_S = 20.0  # the gaze estimate sliding down while the eyes rest, for the second set of runs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    out: dict = {"display_hz": DISPLAY_HZ, "calibration_px": [85, 170], "seeds": args.seeds, "results": {}}
    runs = [(fps, 0.0) for fps in (30.0, 60.0)] + [(30.0, DRIFT_PX_S)]
    for fps, drift in runs:
        for name, make in METHODS.items():
            result = summary(make, fps, args.seeds, drift_px_s=drift)
            key = f"{name} @ {fps:.0f} fps" + (f", gaze sliding {drift:.0f} px/s" if drift else "")
            out["results"][key] = result
            print(key, json.dumps(result), flush=True)
    if args.out:
        args.out.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()

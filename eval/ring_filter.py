"""How still is the gaze ring while the eyes rest, and how quickly does it follow a jump?

Simulated webcam gaze (the error model of eval/gaze_sim.py, fitted to the PC's calibration: 85 px
median error, 170 px 90th percentile; per frame that is 49 px of jitter correlated over ~65 ms, 8%
of frames with a landmark failure of ~200 px, a slow head drift and the calibration's bias) for two
kinds of looking:

- reading: lines of 9 fixations of 180-280 ms, 60-120 px apart, then a return sweep to the next line;
- looking around: fixations of 300-900 ms at random points, saccades of 300-1,800 px;
- nearby: fixations of 250-700 ms on points 120-300 px apart (a toolbar, a menu, a form).

Saccades take 25-70 ms. The tracker gets one gaze sample per camera frame; the ring is drawn at the
display's rate (240 Hz here) the way the overlay's ``Trail`` draws it: the filter's output one
sample interval ago, linearly between the samples around that moment.

Measured at every display frame:

- shake: RMS movement of the ring between consecutive display frames while the eyes rest (from
  100 ms into a fixation): what reads as "too sensitive";
- spread: RMS distance of the ring from its own mean within each fixation;
- jump latency: for saccades of 300 px or more, the time from the start of the saccade until the
  ring has covered 70% of it (median and 90th percentile), and for those of 120-300 px (near);
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


def script(rng: random.Random, seconds: float) -> np.ndarray:
    """Looks as rows of (start, end, x, y); saccades are the gaps between them."""
    looks, t = [], 0.0
    while t < seconds:
        if rng.random() < 0.5:  # a paragraph of reading
            x0, y = rng.uniform(300, 900), rng.uniform(200, 1100)
            for _ in range(5):
                x = x0
                for _ in range(9):
                    d = rng.uniform(0.18, 0.28)
                    looks.append((t, t + d, x, y + rng.gauss(0, 4)))
                    t += d + rng.uniform(0.025, 0.04)
                    x += rng.uniform(60, 120)
                y += 36
                t += rng.uniform(0.03, 0.05)
        elif rng.random() < 0.6:
            for _ in range(8):
                d = rng.uniform(0.3, 0.9)
                looks.append((t, t + d, rng.uniform(100, MONITOR[0] - 100), rng.uniform(100, MONITOR[1] - 100)))
                t += d + rng.uniform(0.03, 0.07)
        else:  # nearby targets
            x, y = rng.uniform(400, MONITOR[0] - 400), rng.uniform(400, MONITOR[1] - 400)
            for _ in range(8):
                d = rng.uniform(0.25, 0.7)
                looks.append((t, t + d, x, y))
                t += d + rng.uniform(0.025, 0.045)
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


def run(make: Callable[[], Filter], seed: int, fps: float, seconds: float = 120.0) -> dict:
    """One simulated session through the filter ``make()`` builds."""
    rng = random.Random(seed)
    looks = script(rng, seconds)
    camera = Camera(ErrorModel.fit(85.0, 170.0), random.Random(seed + 1), MONITOR)
    flt = make()
    ts = np.arange(0.0, seconds, 1.0 / fps)
    eyes = truth(looks, ts)[0]
    out = np.empty((len(ts), 2))
    spent = 0.0
    for n, (t, (tx, ty)) in enumerate(zip(ts.tolist(), eyes.tolist(), strict=True)):
        rx, ry = camera.predict(t, (tx, ty))
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
    return {
        "shake_px": shake,
        "spread_px": float(np.sqrt(np.mean(spreads))),
        "jump_latency_ms": float(np.median(far)),
        "jump_latency_p90_ms": float(np.percentile(far, 90)),
        "near_jump_latency_ms": float(np.median(near)),
        "error_px": float(np.median(np.hypot(*(ring - eye).T)[rest])),
        "us_per_sample": spent / len(ts) * 1e6,
    }


def summary(make: Callable[[], Filter], fps: float, seeds: int) -> dict:
    runs = [run(make, 1000 + s, fps) for s in range(seeds)]
    return {k: round(float(np.median([r[k] for r in runs])), 1) for k in runs[0]}


METHODS: dict[str, Callable[[], Filter]] = {
    "one euro (before)": Smoother,
    "stabilizer": lambda: Stabilizer(MONITOR[0]),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    out: dict = {"display_hz": DISPLAY_HZ, "calibration_px": [85, 170], "seeds": args.seeds, "results": {}}
    for fps in (30.0, 60.0):
        for name, make in METHODS.items():
            result = summary(make, fps, args.seeds)
            out["results"][f"{name} @ {fps:.0f} fps"] = result
            print(f"{name:>18} @ {fps:.0f} fps", json.dumps(result), flush=True)
    if args.out:
        args.out.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()

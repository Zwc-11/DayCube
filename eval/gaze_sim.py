"""Webcam gaze, simulated end to end: does a stare at a paragraph become a dwell, and does normal
reading stay quiet?

Only the eyes and the camera are simulated. A scripted observer looks at a page the way people do
(a stare is many short fixations inside the paragraph; reading moves along each line at a set
speed). A webcam error model turns each frame's true gaze point into what EyeTrax would predict.
From there the real code runs: the tracker loop (``gaze.track.Tracker``) with its smoother and
fixation detector, then the receiver's attention (``capture.attention``), fed browser records
(text snapshots, heartbeats, focus changes, clicks) and fixation batches in the order and with the
latency the live services have.

The error model, per axis, in pixels of the calibrated monitor:

- calibration bias: a smooth field over the screen (an offset plus a linear term), drawn once per
  session;
- head drift since calibration: an Ornstein-Uhlenbeck process, slow (90 s);
- frame jitter: AR(1) with correlation 0.6 between frames at 30 frames/s;
- landmark failures: an occasional frame with a much larger error;
- blinks: 15 a minute, 0.1-0.3 s each, which the tracker skips.

Bias, jitter and failures are fitted so that the per-frame error at held-out targets has the median
and 90th percentile the calibration reported (``--profile``). Drift is not in that measurement,
since the held-out targets follow training within seconds; it is what makes live error larger.

Page layouts are measured by the extension's own reporter in Chromium
(``eval/measure_pages.mjs`` -> ``eval/fixtures/gaze-pages.json``).

    uv run python eval/gaze_sim.py                       # this checkout
    uv run python eval/gaze_sim.py --compare 80e2d6b     # this checkout against a revision

``--compare`` extracts the tracker and receiver modules of the revision into a scratch folder and
runs the same scenarios, seeds and error draws through them, so the numbers differ only by code.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile
from bisect import bisect_right
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = json.loads((ROOT / "eval/fixtures/gaze-pages.json").read_text(encoding="utf-8"))["pages"]
T0 = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
FPS = 30.0
MONITOR = (2560, 1440)  # the workstation's calibrated monitor (docs/capture-windows-check.md)
BROWSER_TOP_DIP = 87.0  # Opera's tab strip and address bar, maximized
TASKBAR_DIP = 48.0
DELIVERY_S = 1.0  # the extension flushes its queue a second after a record is made
LINE_PX = {"example-domain": 20.0, "reading": 28.8}  # line height of body text, CSS px
BUMP = 0.73  # height SD of each bias bump, in bias SDs: the three make 30% of the variance at held-out targets
# The attention parameters were chosen on seeds 0-39; reported results use seeds from 1000 on.
SEED_BASE = 1000

PROFILES = {
    # Held-out per-frame error, median and 90th percentile, from the Windows check on 2026-10-23.
    "first-calibration": (182.9, 482.0),
    "recalibrated": (69.1, 132.9),
    "good-webcam": (40.0, 75.0),
}


# -- the error model ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorModel:
    bias_px: float  # SD of the calibration bias at a point
    jitter_px: float  # marginal SD of frame jitter
    rho: float  # frame-to-frame correlation of jitter at 30 frames/s
    outlier: float  # share of frames with a landmark failure
    outlier_px: float  # SD of the extra error on those frames
    drift_px: float  # stationary SD of head drift
    drift_tau_s: float
    blinks_per_min: float = 15.0

    @staticmethod
    def fit(median_px: float, p90_px: float, *, bias_share: float = 0.5, drift_share: float = 0.5) -> ErrorModel:
        """Bias, jitter and failure rate whose per-frame error has this median and 90th percentile."""
        rng = random.Random(7)
        draws = [(rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1), rng.random(), rng.gauss(0, 1),
                  rng.gauss(0, 1)) for _ in range(20000)]  # fmt: skip

        def quantiles(s: float, q: float) -> tuple[float, float]:
            b, j, o = math.sqrt(bias_share) * s, math.sqrt(1 - bias_share) * s, 3.0 * s
            errors = sorted(
                math.hypot(b * bx + j * jx + (o * ox if u < q else 0), b * by + j * jy + (o * oy if u < q else 0))
                for bx, by, jx, jy, u, ox, oy in draws
            )
            return errors[len(errors) // 2], errors[int(len(errors) * 0.9)]

        def solve(f: Callable[[float], float], target: float, lo: float, hi: float) -> float:
            for _ in range(40):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if f(mid) < target else (lo, mid)
            return (lo + hi) / 2

        s, q = median_px / 1.18, 0.0
        for _ in range(4):
            s = solve(lambda v, q=q: quantiles(v, q)[0], median_px, 1.0, 2 * median_px)
            q = solve(lambda v, s=s: quantiles(s, v)[1], p90_px, 0.0, 0.5) if quantiles(s, 0.0)[1] < p90_px else 0.0
        return ErrorModel(
            bias_px=math.sqrt(bias_share) * s, jitter_px=math.sqrt(1 - bias_share) * s, rho=0.6, outlier=q,
            outlier_px=3.0 * s, drift_px=drift_share * s, drift_tau_s=90.0,
        )  # fmt: skip


class Camera:
    """What EyeTrax would predict, frame by frame, for one session."""

    def __init__(self, model: ErrorModel, rng: random.Random, monitor: tuple[int, int] = MONITOR) -> None:
        self.m, self.rng = model, rng
        w, h = monitor
        self.center = (w / 2, h / 2)
        self.half = (w / 2, h / 2)
        # The calibration bias: an offset and a linear term (70% of its variance), plus three broad
        # bumps (30%) that a correction learned at a few clicks does not capture exactly.
        b0, g = model.bias_px * math.sqrt(0.42), model.bias_px * math.sqrt(0.63)
        self.bias = [(rng.gauss(0, b0), rng.gauss(0, g), rng.gauss(0, g)) for _ in range(2)]
        self.bumps = [
            [(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.gauss(0, model.bias_px * BUMP)) for _ in range(3)]
            for _ in range(2)
        ]
        self.drift = [rng.gauss(0, model.drift_px), rng.gauss(0, model.drift_px)]
        self.jitter = [rng.gauss(0, model.jitter_px), rng.gauss(0, model.jitter_px)]
        self.t: float | None = None

    def predict(self, t: float, point: tuple[float, float]) -> tuple[float, float]:
        m, rng = self.m, self.rng
        dt = 0.0 if self.t is None else max(t - self.t, 0.0)
        self.t = t
        decay = math.exp(-dt / m.drift_tau_s)
        rho = m.rho ** (dt * FPS) if dt else 1.0
        u = (point[0] - self.center[0]) / self.half[0]
        v = (point[1] - self.center[1]) / self.half[1]
        out = []
        for axis in range(2):
            self.drift[axis] = self.drift[axis] * decay + rng.gauss(0, m.drift_px * math.sqrt(1 - decay * decay))
            self.jitter[axis] = self.jitter[axis] * rho + rng.gauss(0, m.jitter_px * math.sqrt(1 - rho * rho))
            b0, gu, gv = self.bias[axis]
            bumps = sum(a * math.exp(-((u - cu) ** 2 + (v - cv) ** 2) / 0.5) for cu, cv, a in self.bumps[axis])
            error = b0 + gu * u + gv * v + bumps + self.drift[axis] + self.jitter[axis]
            if rng.random() < m.outlier:
                error += rng.gauss(0, m.outlier_px)
            out.append(point[axis] + error)
        return out[0], out[1]


# -- the screen and the page -------------------------------------------------------------------


@dataclass(frozen=True)
class Screen:
    dip_scale: float = 1.25  # Windows display scaling
    zoom: float = 2.0  # page zoom

    @property
    def dip(self) -> tuple[float, float]:
        return MONITOR[0] / self.dip_scale, MONITOR[1] / self.dip_scale

    @property
    def viewport(self) -> tuple[float, float]:
        """Viewport size in CSS px, Opera maximized."""
        w, h = self.dip
        return w / self.zoom, (h - BROWSER_TOP_DIP - TASKBAR_DIP) / self.zoom

    def geometry(self, scroll_y: float, scroll_h: float) -> dict:
        w, h = self.dip
        vw, vh = self.viewport
        return {
            "vx": 0.0, "vy": BROWSER_TOP_DIP, "scale": self.zoom, "inner_w": round(vw, 1), "inner_h": round(vh, 1),
            "screen_w": round(w, 1), "screen_h": round(h, 1), "primary": True, "exact": True,
            "scroll_y": scroll_y, "scroll_h": scroll_h,
        }  # fmt: skip

    def to_monitor(self, x: float, y: float) -> tuple[float, float]:
        """A viewport point in CSS px to physical pixels of the monitor."""
        return x * self.zoom * self.dip_scale, (BROWSER_TOP_DIP + y * self.zoom) * self.dip_scale


@dataclass(frozen=True)
class Layout:
    name: str
    screen: Screen
    blocks: tuple[dict, ...]  # whole-page rects in CSS px (page coordinates at scroll 0)
    scroll_h: float

    @staticmethod
    def of(name: str, screen: Screen) -> Layout:
        widths = {int(w): page for w, page in PAGES[name].items()}
        width = min(widths, key=lambda w: abs(w - screen.viewport[0]))
        page = widths[width]
        return Layout(name, screen, tuple(page["blocks"]), page["scroll_h"])

    def block(self, key: str) -> dict:
        """A block by tag and order ("p2") or by its id."""
        for b in self.blocks:
            if b["id"] == key:
                return b
        tag, index = key.rstrip("0123456789"), int(key[len(key.rstrip("0123456789")) :] or 1)
        return [b for b in self.blocks if b["tag"] == tag][index - 1]

    def visible(self, scroll_y: float, sent: set[str]) -> list[dict]:
        """What the reporter sends at this scroll: blocks on screen, rects clipped to the viewport."""
        vw, vh = self.screen.viewport
        out = []
        for b in self.blocks:
            left, top, width, height = b["rect"]
            top -= scroll_y
            if top + height < 0 or top > vh or left > vw:
                continue
            t, bottom = max(0.0, top), min(vh, top + height)
            block = {
                "id": b["id"],
                "tag": b["tag"],
                "rect": [left, round(t, 1), min(width, vw - left), round(bottom - t, 1)],
            }
            if b["id"] not in sent:
                block |= {"text": b["text"], "words": b["words"], "lines": b.get("lines") or []}
                sent.add(b["id"])
            out.append(block)
        return out


# -- the observer ------------------------------------------------------------------------------


@dataclass
class Script:
    """What happens: where the eyes are (viewport CSS px, or None off screen) and browser records."""

    layout: Layout
    looks: list[tuple[float, float, float | None, float | None]] = field(default_factory=list)
    truth: list[tuple[str, int] | None] = field(default_factory=list)  # (block, line) being read, per look
    records: list[tuple[float, str, dict]] = field(default_factory=list)
    scroll: list[tuple[float, float]] = field(default_factory=lambda: [(0.0, 0.0)])
    t: float = 0.0

    def scroll_at(self, t: float) -> float:
        return self.scroll[bisect_right([s for s, _ in self.scroll], t) - 1][1]

    def look(self, seconds: float, x: float | None, y: float | None, truth: tuple[str, int] | None = None) -> None:
        self.looks.append((self.t, self.t + seconds, x, y))
        self.truth.append(truth)
        self.t += seconds

    def stare(self, block: dict, seconds: float, rng: random.Random) -> None:
        """Fixations of about 0.5 s anywhere in the middle of the block, as a stare really is."""
        left, top, width, height = block["rect"]
        end = self.t + seconds
        while self.t < end - 1e-6:
            d = min(max(rng.lognormvariate(math.log(0.45), 0.4), 0.12), 2.0, end - self.t)
            x = left + width * rng.uniform(0.2, 0.8)
            y = top - self.scroll_at(self.t) + height * rng.uniform(0.25, 0.75)
            self.look(d, x, y)

    def read(self, block: dict, rng: random.Random, wpm: float = 238.0) -> None:
        """Line by line at ``wpm`` over the block's measured lines: about one fixation per 1.2 words
        along each line, with one regression (a look back a few words) in ten."""
        lines = block.get("lines") or [{"rect": block["rect"], "text": block["text"]}]
        counts = [max(1, len(line["text"].split())) for line in lines]
        budget = sum(counts) * 60.0 / wpm
        for i, (line, n) in enumerate(zip(lines, counts, strict=True)):
            x0, top, width, height = line["rect"]
            stops = max(1, round(n / 1.2))
            weights = [max(0.4, rng.gauss(1.0, 0.3)) for _ in range(stops)]
            share = budget * n / sum(counts)
            y = top - self.scroll_at(self.t) + height / 2
            for k, w in enumerate(weights):
                position = (k + 0.5) / stops
                if k and rng.random() < 0.1:
                    position = max(0.0, position - rng.uniform(0.1, 0.3))
                x = x0 + width * (0.03 + 0.94 * position) + rng.gauss(0, 6)
                self.look(share * w / sum(weights), x, y, (block["id"], i))

    def scroll_to(self, page_y: float) -> None:
        """Scroll so ``page_y`` is near the top; the reporter re-measures 350 ms after scrolling."""
        y = max(0.0, min(page_y - 40, self.layout.scroll_h - self.layout.screen.viewport[1]))
        self.scroll.append((self.t, y))
        self.records.append((self.t + 0.35, "blocks", {}))

    def record(self, kind: str, **fields: object) -> None:
        self.records.append((self.t, kind, fields))


@dataclass(frozen=True)
class Scenario:
    name: str
    page: str
    zoom: float
    build: Callable[[Script, random.Random], None]
    target: str | None  # the block a dwell should fire on, or None for "no dwell at all"
    note: str


def _load(script: Script) -> None:
    """A page load: first snapshot after 600 ms, the mouse moved across the page (exact geometry)."""
    script.records.append((0.6, "blocks", {}))
    script.look(1.5, script.layout.screen.viewport[0] / 2, script.layout.screen.viewport[1] / 2)


def _clicks(script: Script, rng: random.Random, n: int = 6) -> None:
    """Browsing before the test: look near a link, click it 0.3 s later, read around. The eyes land
    within about 15 CSS px of the click, and one click in seven is made without looking."""
    vw, vh = script.layout.screen.viewport
    for _ in range(n):
        x, y = rng.uniform(0.1, 0.9) * vw, rng.uniform(0.1, 0.9) * vh
        if rng.random() < 1 / 7:
            ex, ey = rng.uniform(0.1, 0.9) * vw, rng.uniform(0.1, 0.9) * vh
        else:
            ex, ey = x + rng.gauss(0, 15), y + rng.gauss(0, 15)
        script.look(0.35, ex, ey)
        script.record("click", x=round(x, 1), y=round(y, 1))
        script.look(0.25, ex, ey)
        script.look(rng.uniform(2, 6), rng.uniform(0.1, 0.9) * vw, rng.uniform(0.1, 0.9) * vh)


def _paragraph(seconds: float, clicks: bool = False) -> Callable[[Script, random.Random], None]:
    def build(s: Script, rng: random.Random) -> None:
        if clicks:
            _clicks(s, rng)
        _load(s)
        s.stare(s.layout.block("p1"), seconds, rng)
        s.look(3.0, None, None)

    return build


def _look_away(s: Script, rng: random.Random) -> None:
    _load(s)
    s.stare(s.layout.block("p1"), 8, rng)
    s.look(1.5, None, None)
    s.stare(s.layout.block("p1"), 12, rng)
    s.look(3.0, None, None)


def _tab_switch(s: Script, rng: random.Random) -> None:
    _load(s)
    s.stare(s.layout.block("p1"), 8, rng)
    s.record("focus", away=True)
    s.look(3.0, None, None)
    s.record("focus", away=False)
    s.records.append((s.t + 0.2, "blocks", {}))
    s.stare(s.layout.block("p1"), 8, rng)
    s.look(3.0, None, None)


def _reading(s: Script, rng: random.Random) -> None:
    """Read the article top to bottom at normal speed, scrolling as a reader does."""
    _load(s)
    vh = s.layout.screen.viewport[1]
    for b in s.layout.blocks:
        if b["tag"] == "a" or b["text"].startswith("Read the next"):
            continue
        top, height = b["rect"][1], b["rect"][3]
        if top + height - s.scroll_at(s.t) > vh * 0.9:
            s.scroll_to(top)
        s.read(b, rng, wpm=rng.uniform(200, 280))
    s.look(3.0, None, None)


def _reread(s: Script, rng: random.Random) -> None:
    _load(s)
    block = s.layout.block("p2")
    for _ in range(3):
        s.read(block, rng)
    s.look(3.0, None, None)


def _heading_then_paragraph(s: Script, rng: random.Random) -> None:
    """A long paragraph read slowly right under a two-word heading: no dwell on the heading."""
    _load(s)
    s.read(s.layout.block("p2"), rng, wpm=170)
    s.look(3.0, None, None)


SCENARIOS = [
    Scenario(
        "stare 20 s, 200% zoom",
        "example-domain",
        2.0,
        _paragraph(20),
        "p1",
        "The Windows paragraph test: example.com at 200%, 20 s on the body paragraph.",
    ),
    Scenario(
        "stare 20 s, 200% zoom, after 6 clicks",
        "example-domain",
        2.0,
        _paragraph(20, clicks=True),
        "p1",
        "The same test after a minute of browsing with six clicks.",
    ),
    Scenario(
        "stare 20 s, 100% zoom",
        "example-domain",
        1.0,
        _paragraph(20),
        "p1",
        "The same paragraph at normal zoom: 40 CSS px tall.",
    ),
    Scenario(
        "look away 1.5 s mid-stare",
        "example-domain",
        2.0,
        _look_away,
        "p1",
        "8 s, eyes off screen for 1.5 s, 12 s more: one dwell.",
    ),
    Scenario(
        "re-read a paragraph 3 times",
        "reading",
        1.0,
        _reread,
        "p2",
        "98 words read three times at 238 wpm: more than twice the reading time.",
    ),
    Scenario(
        "glance 5 s", "example-domain", 2.0, _paragraph(5), None, "Five seconds on the paragraph: below the 8 s floor."
    ),
    Scenario(
        "tab switch mid-stare",
        "example-domain",
        2.0,
        _tab_switch,
        None,
        "8 s, another tab for 3 s, 8 s: two runs, neither long enough.",
    ),
    Scenario(
        "read an article",
        "reading",
        1.0,
        _reading,
        None,
        "Top to bottom at 200-280 wpm with scrolling: reading is not a dwell.",
    ),
    Scenario(
        "read an article, 200% zoom",
        "reading",
        2.0,
        _reading,
        None,
        "The same article at 200% zoom: about two screens, so the reader scrolls on the way.",
    ),
    Scenario(
        "slow read under a heading",
        "reading",
        1.0,
        _heading_then_paragraph,
        None,
        "98 words at 170 wpm directly under a two-word heading.",
    ),
]


# -- running the real code ---------------------------------------------------------------------


class SimClock:
    def __init__(self, t: float, rng: random.Random) -> None:
        self.now, self.rng = t, rng

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0) + self.rng.uniform(0.0, 0.002)


class Eyes:
    """The tracker's camera and estimator: a frame is the time it was taken."""

    def __init__(self, script: Script, camera: Camera, clock: SimClock, rng: random.Random, blinks: float) -> None:
        self.script, self.camera, self.clock = script, camera, clock
        self.starts = [s for s, _, _, _ in script.looks]
        self.blinks: list[tuple[float, float]] = []
        t = -5.0
        while t < script.t + 5:
            t += rng.expovariate(blinks / 60.0)
            self.blinks.append((t, t + rng.uniform(0.1, 0.3)))

    def read(self) -> tuple[bool, float]:
        return True, self.clock.now

    def release(self) -> None:
        pass

    def extract_features(self, t: float) -> tuple[object, bool]:
        blink = any(a <= t < b for a, b in self.blinks)
        return t, blink

    def predict(self, features: object) -> list[list[float]]:
        t = float(features[0][0])
        i = bisect_right(self.starts, t) - 1
        layout = self.script.layout
        if i < 0 or t >= self.script.looks[i][1] or self.script.looks[i][2] is None:
            point = (MONITOR[0] / 2, MONITOR[1] * 1.3)  # off screen: below it
        else:
            _, _, x, y = self.script.looks[i]
            point = layout.screen.to_monitor(x, y)
        return [list(self.camera.predict(t, point))]


class Tracked:
    """The tracker's link to the receiver: fixations queue up and go out with each report."""

    def __init__(self, clock: SimClock) -> None:
        self.clock = clock
        self.queue: list = []
        self.batches: list[tuple[float, list, dict]] = []

    def add(self, fixation: object) -> None:
        self.queue.append(fixation)

    def send(self, state: dict) -> str:
        self.batches.append((self.clock.now, self.queue, state))
        self.queue = []
        return "ok"


class Quiet:
    def idle_seconds(self) -> float:
        return 0.0

    def locked(self) -> bool:
        return False

    def camera_busy(self) -> bool:
        return False


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


@dataclass
class Outcome:
    fired: list[tuple[float, str, float]]  # time, block, dwell_s of each dwell start
    fixation_s: float  # seconds covered by fixations
    tracked_s: float  # seconds the eyes were on the screen
    on_target_s: float  # fixation seconds placed on the target block
    reading_s: float = 0.0  # fixation seconds during reading, with a known true line
    line_s: float = 0.0  # ... placed on the right block and the right line
    near_s: float = 0.0  # ... on the right block, within one line
    read: set[str] = field(default_factory=set)  # blocks really read for at least 1.5 s
    recorded: set[str] = field(default_factory=set)  # blocks with a record in reading.jsonl
    # Reading-line labels for the tracker (capture/labels.py): each one's vertical distance from
    # where the eyes really were (monitor px), and the reading time they came from.
    label_errors: list[float] = field(default_factory=list)
    label_line_px: float = 0.0  # the body text's line pitch on the monitor, to judge them by


def run(scenario: Scenario, model: ErrorModel, seed: int, screen: Screen) -> Outcome:
    import numpy as np  # noqa: F401 - the tracker needs it
    from capture import attention as attention_module
    from capture import records
    from gaze.track import Tracker

    rng = random.Random(seed)
    layout = Layout.of(scenario.page, replace(screen, zoom=scenario.zoom))
    script = Script(layout)
    scenario.build(script, rng)
    target = layout.block(scenario.target)["id"] if scenario.target else None

    clock = SimClock(-3.0, rng)
    eyes = Eyes(script, Camera(model, random.Random(seed * 7919 + 1)), clock, rng, model.blinks_per_min)
    link = Tracked(clock)
    calibration = {"monitor": list(MONITOR), "error_px": None, "camera": 0, "model": "ridge"}
    calibration["error_px"] = round(_median_error(model), 1)
    tracker = Tracker(
        eyes, lambda: eyes, link, calibration, system=Quiet(), fps=FPS, clock=clock, sleep=clock.sleep, timer=clock
    )
    tracker.confirmed_at = -3.0
    while clock.now < script.t + 2.0:
        tracker.step()

    # Browser records, as the receiver would get them.
    deliveries: list[tuple[float, int, object]] = []
    sent: set[str] = set()
    tab, other = 7, 8
    base = {"tab": tab, "window": 1, "url": f"https://example.test/{layout.name}", "title": layout.name}
    view_t = 5.0
    order = 0
    events = sorted(script.records + [(t, "view", {}) for t in _range(view_t, script.t, 5.0)], key=lambda r: r[0])
    away = False
    for t, kind, fields in events:
        order += 1
        g = layout.screen.geometry(script.scroll_at(t), layout.scroll_h)
        rid = f"r-{order:06d}"
        if kind == "blocks" and not away:
            visible = layout.visible(g["scroll_y"], sent)
            if "lines" not in records.Block.model_fields:  # a revision from before line decoding
                visible = [{k: v for k, v in b.items() if k != "lines"} for b in visible]
            rec = records.Blocks.model_validate(
                base | {"id": rid, "t": _at(t), "kind": "blocks", "doc": "doc-0001", "geometry": g,
                        "blocks": visible}
            )  # fmt: skip
        elif kind == "view" and not away:
            rec = records.View.model_validate(
                base | {"id": rid, "t": _at(t), "kind": "view", "doc": "doc-0001", "geometry": g}
            )
        elif kind == "focus":
            away = bool(fields["away"])
            rec = records.Focus(
                id=rid, t=_at(t), kind="focus", tab=other if away else tab, window=1, url=base["url"], title="x"
            )
        elif kind == "click":
            click = base | {"id": rid, "t": _at(t), "kind": "click", "href": None, "text": "link"}
            if "x" in records.Click.model_fields:
                click |= {"x": fields["x"], "y": fields["y"], "geometry": g}
            rec = records.Click.model_validate(click)
        else:
            continue
        deliveries.append((t + DELIVERY_S, 0, rec))
    for when, batch, state in link.batches:
        deliveries.append((when, 1, (batch, state)))
    deliveries.sort(key=lambda d: (d[0], d[1]))

    try:
        from capture import reading as reading_module
    except ImportError:  # a revision from before line decoding
        reading_module = None
    for name, value in json.loads(os.environ.get("GAZE_SIM_READING", "{}")).items():  # tuning only
        setattr(reading_module, name, value)
    options = json.loads(os.environ.get("GAZE_SIM_ATTENTION", "{}"))
    accepted = attention_module.Attention.__init__.__code__.co_varnames
    attention = attention_module.Attention(**{k: v for k, v in options.items() if k in accepted})
    fired: list[tuple[float, str, float]] = []
    recorded: set[str] = set()
    label_errors: list[float] = []
    eyes_at: dict[str, float] = {}  # fixation id -> where the eyes really were (monitor px, y), while reading
    fixation_s = on_target = reading_s = line_s = near_s = 0.0
    starts = [a for a, _, _, _ in script.looks]
    count = 0
    for when, kind, payload in deliveries:
        if kind == 0:
            out = attention.browser(payload)
        else:
            batch, state = payload
            gaze_state = records.GazeState.model_validate(state)
            if hasattr(attention, "gaze_state"):
                attention.gaze_state(gaze_state)
            else:
                attention.gaze_monitor(gaze_state.monitor)
            out = []
            for f in batch:
                count += 1
                fix = records.Fixation(
                    id=f"f-{count:07d}", t=_at(f.start), t_end=_at(f.end), x=f.x, y=f.y, samples=f.samples
                )
                hit, events_ = attention.fixation(fix)
                out += events_
                fixation_s += f.end - f.start
                if hit is not None and target is not None and hit.block == target:
                    on_target += f.end - f.start
                i = bisect_right(starts, (f.start + f.end) / 2) - 1  # the look in the middle of the fixation
                truth = script.truth[i] if 0 <= i < len(script.truth) else None
                if truth is not None and script.looks[i][3] is not None:
                    eyes_at[fix.id] = layout.screen.to_monitor(script.looks[i][2], script.looks[i][3])[1]
                if truth is not None:
                    reading_s += f.end - f.start
                    on = hit is not None and getattr(hit, "line_block", getattr(hit, "block", None)) == truth[0]
                    line = getattr(hit, "line", None) if on else None
                    if line is not None:
                        line_s += (f.end - f.start) * (line == truth[1])
                        near_s += (f.end - f.start) * (abs(line - truth[1]) <= 1)
            out += attention.advance(_at(when) - timedelta(seconds=3))
            for label in attention.labels.drain() if hasattr(attention, "labels") else []:
                if label["id"] in eyes_at:  # a label from a look that was not reading is wrong by construction
                    label_errors.append(abs(label["y"] * MONITOR[1] - eyes_at[label["id"]]))
                else:
                    label_errors.append(math.inf)
        for e in out:
            if e.get("kind") == "read":
                recorded.add(e["block"])
            if e.get("kind", "dwell") == "dwell" and e["phase"] == "start":
                fired.append(
                    (round((datetime.fromisoformat(e["t_end"]) - T0).total_seconds(), 2), e["block"], e["dwell_s"])
                )
    if hasattr(attention, "end_visit"):
        for e in attention.end_visit():
            recorded.add(e["block"])
    tracked = sum(b - a for a, b, x, _ in script.looks if x is not None)
    per_block: dict[str, float] = {}
    for (a, b, _, _), truth in zip(script.looks, script.truth, strict=True):
        if truth is not None:
            per_block[truth[0]] = per_block.get(truth[0], 0.0) + b - a
    read = {block for block, seconds in per_block.items() if seconds >= 1.5}
    pitch = LINE_PX.get(layout.name, 20.0) * layout.screen.zoom * layout.screen.dip_scale
    return Outcome(
        fired, fixation_s, tracked, on_target, reading_s, line_s, near_s, read, recorded, label_errors, pitch
    )


def _range(start: float, stop: float, step: float) -> Iterator[float]:
    t = start
    while t < stop:
        yield t
        t += step


@cache
def _median_error(model: ErrorModel) -> float:
    """The held-out median error this model implies (what the tracker reports as error_px)."""
    rng = random.Random(3)
    errors = []
    for _ in range(4000):
        ex = model.bias_px * rng.gauss(0, 1) + model.jitter_px * rng.gauss(0, 1)
        ey = model.bias_px * rng.gauss(0, 1) + model.jitter_px * rng.gauss(0, 1)
        if rng.random() < model.outlier:
            ex, ey = ex + rng.gauss(0, model.outlier_px), ey + rng.gauss(0, model.outlier_px)
        errors.append(math.hypot(ex, ey))
    errors.sort()
    return errors[len(errors) // 2]


# -- the report --------------------------------------------------------------------------------


@cache
def _model(profile: str) -> ErrorModel:
    return ErrorModel.fit(*PROFILES[profile])


def _trial(job: tuple[int, str, float, int]) -> dict:
    index, profile, dip_scale, seed = job
    scenario = SCENARIOS[index]
    model = _model(profile)
    out = run(scenario, model, seed, Screen(dip_scale=dip_scale))
    target = (
        Layout.of(scenario.page, Screen(dip_scale=dip_scale, zoom=scenario.zoom)).block(scenario.target)["id"]
        if scenario.target
        else None
    )
    right = [f for f in out.fired if f[1] == target]
    wrong = [f for f in out.fired if f[1] != target]
    return {
        "scenario": scenario.name, "profile": profile, "dip_scale": dip_scale, "seed": seed,
        "right": bool(right), "wrong": bool(wrong), "first_s": right[0][0] if right else None,
        "coverage": out.fixation_s / out.tracked_s if out.tracked_s else 0.0,
        "reading_s": out.reading_s, "line_s": out.line_s, "near_s": out.near_s,
        "read": len(out.read), "recorded": len(out.read & out.recorded), "extra": len(out.recorded - out.read),
        "on_target": out.on_target_s / out.tracked_s if out.tracked_s and target else None,
    }  # fmt: skip


def evaluate(seeds: int, profiles: list[str], scales: list[float], workers: int) -> list[dict]:
    first = int(os.environ.get("GAZE_SIM_FIRST_SEED", SEED_BASE))
    only = os.environ.get("GAZE_SIM_ONLY")  # run only scenarios whose name contains this
    jobs = [
        (i, p, s, seed)
        for i in range(len(SCENARIOS))
        if not only or only in SCENARIOS[i].name
        for p in profiles
        for s in scales
        for seed in range(first, first + seeds)
    ]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_trial, jobs, chunksize=8))


def summarize(trials: list[dict]) -> list[dict]:
    rows: dict[tuple, list[dict]] = {}
    for t in trials:
        rows.setdefault((t["scenario"], t["profile"], t["dip_scale"]), []).append(t)
    out = []
    for (name, profile, scale), group in rows.items():
        scenario = next(s for s in SCENARIOS if s.name == name)
        n = len(group)
        firsts = sorted(t["first_s"] for t in group if t["first_s"] is not None)
        out.append({
            "scenario": name, "profile": profile, "dip_scale": scale, "trials": n,
            "expect": "dwell on target" if scenario.target else "no dwell",
            "pass": sum((t["right"] and not t["wrong"]) if scenario.target else not (t["right"] or t["wrong"]) for t in group) / n,
            "fired_on_target": sum(t["right"] for t in group) / n,
            "fired_elsewhere": sum(t["wrong"] for t in group) / n,
            "median_time_to_dwell_s": firsts[len(firsts) // 2] if firsts else None,
            "fixation_coverage": sum(t["coverage"] for t in group) / n,
            "on_target_share": (sum(t["on_target"] for t in group) / n) if scenario.target else None,
            # Reading time on the right line (and within one line), pooled over sessions.
            "line_exact": (sum(t["line_s"] for t in group) / reading) if (reading := sum(t["reading_s"] for t in group)) else None,
            "line_near": (sum(t["near_s"] for t in group) / reading) if reading else None,
            # Blocks read for 1.5 s or more that got a reading.jsonl record, and records of blocks not read.
            "reads_recorded": (sum(t.get("recorded", 0) for t in group) / read) if (read := sum(t.get("read", 0) for t in group)) else None,
            "extra_reads": sum(t.get("extra", 0) for t in group) / n,
        })  # fmt: skip
    return out


def _extract(revision: str) -> Path:
    """The tracker and receiver modules of a revision, importable from a scratch folder."""
    folder = Path(tempfile.mkdtemp(prefix=f"gaze-sim-{revision}-"))
    sources = {"capture": "services/capture-win/capture", "gaze": "services/gaze/gaze"}
    for package, source in sources.items():
        (folder / package).mkdir()
        listing = subprocess.run(
            ["git", "ls-tree", "--name-only", f"{revision}:{source}"], cwd=ROOT, check=True, capture_output=True,
            text=True,
        ).stdout.split()  # fmt: skip
        for name in [n for n in listing if n.endswith(".py")]:
            text = subprocess.run(
                ["git", "show", f"{revision}:{sources[package]}/{name}"], cwd=ROOT, check=True, capture_output=True,
                text=True,
            ).stdout  # fmt: skip
            (folder / package / name).write_text(text, encoding="utf-8")
    return folder


def _paths(extracted: Path | None) -> None:
    if extracted is not None:
        sys.path[:0] = [str(extracted)]
    else:
        sys.path[:0] = [str(ROOT / "services/capture-win"), str(ROOT / "services/gaze")]


def _child(args: argparse.Namespace) -> None:
    _paths(Path(args.code) if args.code else None)
    trials = evaluate(args.seeds, args.profiles, args.scales, args.workers)
    print(json.dumps(trials))


def _spawn(code: Path | None, args: argparse.Namespace) -> list[dict]:
    command = [sys.executable, __file__, "--child", "--seeds", str(args.seeds), "--workers", str(args.workers),
               "--profiles", *args.profiles, "--scales", *map(str, args.scales)]  # fmt: skip
    if code is not None:
        command += ["--code", str(code)]
    result = subprocess.run(
        command, cwd=ROOT, check=True, capture_output=True, text=True, env=os.environ | {"PYTHONHASHSEED": "0"}
    )
    return json.loads(result.stdout)


def table(rows: list[dict], label: str) -> str:
    lines = [f"## {label}", "", "| Scenario | Calibration | Scaling | Expect | Pass | On target | Elsewhere | Coverage | Placed on target | Time to dwell |",
             "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]  # fmt: skip
    for r in rows:
        share = "" if r["on_target_share"] is None else f"{r['on_target_share']:.0%}"
        first = "" if r["median_time_to_dwell_s"] is None else f"{r['median_time_to_dwell_s']:.1f} s"
        lines.append(
            f"| {r['scenario']} | {r['profile']} | {r['dip_scale']:.0%} | {r['expect']} | {r['pass']:.0%} | "
            f"{r['fired_on_target']:.0%} | {r['fired_elsewhere']:.0%} | {r['fixation_coverage']:.0%} | {share} | {first} |"
        )
    reading = [r for r in rows if r.get("line_exact") is not None]
    if reading:
        lines += ["", "Line reading: share of reading time decoded on the right line and within one line; blocks",
                  "read for 1.5 s or more that got a `reading.jsonl` record; records per session for blocks not read.", "",
                  "| Scenario | Calibration | Scaling | Right line | Within 1 line | Blocks recorded | Extra records |",
                  "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]  # fmt: skip
        for r in reading:
            recorded = "" if r.get("reads_recorded") is None else f"{r['reads_recorded']:.0%}"
            lines.append(
                f"| {r['scenario']} | {r['profile']} | {r['dip_scale']:.0%} | {r['line_exact']:.0%} | "
                f"{r['line_near']:.0%} | {recorded} | {r.get('extra_reads', 0):.1f} |"
            )
    return "\n".join(lines)


class LiveEyes:
    """The live tracker's camera and estimator: a stare at a rectangle of the monitor, in real time."""

    def __init__(self, looks: list[tuple[float, float, float, float]], camera: Camera, blinks: list) -> None:
        self.looks, self.camera, self.blinks = looks, camera, blinks
        self.starts = [a for a, _, _, _ in looks]

    def read(self) -> tuple[bool, float]:
        import time

        return True, time.time()

    def release(self) -> None:
        pass

    def extract_features(self, t: float) -> tuple[object, bool]:
        return t, any(a <= t < b for a, b in self.blinks)

    def predict(self, features: object) -> list[list[float]]:
        t = float(features[0][0])
        i = bisect_right(self.starts, t) - 1
        if i < 0 or t >= self.looks[i][1]:
            point = (self.camera.center[0], self.camera.center[1] * 2.6)  # before and after: off screen
        else:
            point = self.looks[i][2], self.looks[i][3]
        return [list(self.camera.predict(t, point))]


def _read_lines(
    lines: list[list[float]], rng: random.Random, t: float, wpm: float = 238.0
) -> list[tuple[float, float, float, float, int]]:
    """Looks that read ``lines`` ([left, top, width, height, words], monitor px) as ``Script.read``
    does: (start, end, x, y, line)."""
    budget = sum(max(1, int(line[4])) for line in lines) * 60.0 / wpm
    total = sum(max(1, int(line[4])) for line in lines)
    looks = []
    for i, (x0, top, width, height, words) in enumerate(lines):
        n = max(1, int(words))
        stops = max(1, round(n / 1.2))
        weights = [max(0.4, rng.gauss(1.0, 0.3)) for _ in range(stops)]
        share = budget * n / total
        for k, w in enumerate(weights):
            position = (k + 0.5) / stops
            if k and rng.random() < 0.1:
                position = max(0.0, position - rng.uniform(0.1, 0.3))
            d = share * w / sum(weights)
            looks.append((t, t + d, x0 + width * (0.03 + 0.94 * position) + rng.gauss(0, 6), top + height / 2, i))
            t += d
    return looks


def live(args: argparse.Namespace) -> None:
    """Run the real tracker loop in real time against a running receiver, the eyes and camera
    simulated: a stare at ``--rect`` (monitor px) for ``--seconds``, or reading ``--lines`` line by
    line. For the browser end-to-end test (extensions/tests/gaze.e2e.spec.ts)."""
    import time

    sys.path[:0] = [str(ROOT / "services/gaze")]
    from gaze.receiver import Receiver
    from gaze.track import Tracker

    rng = random.Random(args.seed)
    width, height = args.monitor
    held_out = PROFILES[args.profile]
    scale = width / MONITOR[0]  # the same error in degrees on a monitor this wide
    model = ErrorModel.fit(held_out[0] * scale, held_out[1] * scale)
    camera = Camera(model, random.Random(args.seed * 7919 + 1), (width, height))
    begin = time.time() + 3.0  # the tracker turns the camera on once the receiver has answered
    looks, t, read = [], begin, []
    if args.lines:
        read = _read_lines(json.loads(Path(args.lines).read_text(encoding="utf-8")), rng, begin, args.wpm)
        looks, t = [look[:4] for look in read], read[-1][1]
    else:
        left, top, w, h = args.rect
        while t < begin + args.seconds:
            d = min(max(rng.lognormvariate(math.log(0.45), 0.4), 0.12), 2.0)
            looks.append((t, t + d, left + w * rng.uniform(0.2, 0.8), top + h * rng.uniform(0.25, 0.75)))
            t += d
    blinks, b = [], begin
    while b < t:
        b += rng.expovariate(model.blinks_per_min / 60.0)
        blinks.append((b, b + rng.uniform(0.1, 0.3)))
    eyes = LiveEyes(looks, camera, blinks)
    receiver = Receiver(args.receiver, Path(args.token_file))
    calibration = {"monitor": [width, height], "error_px": round(held_out[0] * scale, 1), "camera": 0, "model": "ridge"}
    tracker = Tracker(eyes, lambda: eyes, receiver, calibration, system=Quiet(), fps=FPS)
    while time.time() < t + 2.5:
        tracker.step()
    tracker._release()
    tracker.reported_at = -1e9
    tracker._report(time.time())
    print(json.dumps({
        "looks": len(looks), "seconds": round(t - begin, 1), "error_px": calibration["error_px"],
        "read": [[round(a, 3), round(b, 3), line] for a, b, _, _, line in read],  # epoch seconds, line read
    }))  # fmt: skip


def main() -> None:
    if sys.argv[1:2] == ["live"]:
        parser = argparse.ArgumentParser(prog="gaze_sim.py live", description=live.__doc__.split("\n\n")[0])
        parser.add_argument("live")
        parser.add_argument("--receiver", default="http://127.0.0.1:7700")
        parser.add_argument("--token-file", required=True)
        parser.add_argument("--monitor", nargs=2, type=int, required=True)
        parser.add_argument("--rect", nargs=4, type=float, help="left top width height, monitor px")
        parser.add_argument("--lines", help="JSON file: [[left, top, width, height, words], ...] in monitor px")
        parser.add_argument("--wpm", type=float, default=238.0)
        parser.add_argument("--seconds", type=float, default=20.0)
        parser.add_argument("--profile", default="recalibrated", choices=list(PROFILES))
        parser.add_argument("--seed", type=int, default=1)
        return live(parser.parse_args())
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--profiles", nargs="+", default=list(PROFILES), choices=list(PROFILES))
    parser.add_argument("--scales", nargs="+", type=float, default=[1.0, 1.25], help="Windows display scaling")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    parser.add_argument("--compare", help="A git revision to run the same trials against")
    parser.add_argument("--output", type=Path, help="Write the summary as JSON here")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--code", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return _child(args)

    report: dict = {"profiles": {p: asdict(ErrorModel.fit(*PROFILES[p])) | {"held_out": PROFILES[p]} for p in args.profiles},
                    "seeds": args.seeds, "monitor": MONITOR}  # fmt: skip
    current = summarize(_spawn(None, args))
    report["current"] = current
    print(table(current, "This checkout"))
    if args.compare:
        before = summarize(_spawn(_extract(args.compare), args))
        report["compare"] = {"revision": args.compare, "rows": before}
        print()
        print(table(before, f"Revision {args.compare}"))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

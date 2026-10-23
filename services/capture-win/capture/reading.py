"""Which line of the page the eyes are on, from webcam gaze.

One fixation cannot pick a line: webcam error (a 69 px median on a 2560 px monitor) spans about
two lines of body text at 100% zoom. Reading as a sequence can, because three things survive the
error:

- the tracker's error is mostly an offset that changes slowly (head position, calibration), so
  the *spacing* of fixations is accurate even when their position is not;
- horizontal movement between consecutive fixations is free of that offset. A return sweep (back
  over most of a line, to the start of the line below) means the eyes went to the next line; small
  steps mean they stayed on it;
- the page's own shape: short last lines of paragraphs, headings, the larger gaps between
  paragraphs. A hypothesis that puts the reader a line too low soon has them reading past the end
  of a short line or into a gap.

The decoder is a hidden Markov model over (line, vertical offset): every line of the page that is
in the viewport, "off the text" (images, margins), and a grid of offsets around zero spread by the
tracker's error. Transitions follow reading: stay on the line, go to the line below (likely after a
return sweep, unlikely otherwise), back a line or two, back to the start of the paragraph, or
anywhere (more often to the first line of a block). The offset drifts slowly. Each fixation is
scored against each line's band shifted by the offset, and against the line's horizontal extent.
Filtering forward gives the probability of each line after every fixation; the offset is learned
from the reading itself.

Lines come from the extension (``content.js`` ``linesOf``): rectangles in page coordinates and
the text on each. ``eval/gaze_sim.py`` measures accuracy against simulated reading.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field

SQRT2 = math.sqrt(2)


def _between(a: float, b: float) -> float:
    if b > 0:
        return 0.5 * (math.erfc(b) - math.erfc(a))
    if a < 0:
        return 0.5 * (math.erfc(-a) - math.erfc(-b))
    return 0.5 * (math.erf(a) - math.erf(b))


def _inside(v: float, lo: float, size: float, sigma: float) -> float:
    """The box [lo, lo + size] blurred by a Gaussian of SD ``sigma``, at ``v``: 1 well inside,
    falling to 0 past the edges. Divided by ``size`` it is the density of a point uniform on the
    box plus that error."""
    k = sigma * SQRT2
    return _between((v - lo) / k, (v - lo - max(size, 1.0)) / k)


@dataclass(frozen=True)
class LineBox:
    x: float  # page coordinates, CSS px
    y: float
    w: float
    h: float
    text: str


Key = tuple[str, int]  # (block id, line in block)
OFF = None  # the eyes are not on a line

# Transitions, per fixation. Each is weighed by how well the horizontal step fits it (a density
# in px of step): staying fits steps within the line, the next line a return sweep, the rest
# anything on the screen.
STAY, NEXT, PREV, NEXT2, PREV2, START, TO_OFF, JUMP = 0.72, 0.2, 0.015, 0.01, 0.005, 0.01, 0.01, 0.03
OFF_STAY = 0.7  # from off the text: stay off, else land on a line as a jump does
FIRST = 3.0  # a jump lands on the first line of a block this many times more often
FORWARD, BACK, BACK_SHARE = 0.6, 0.35, 0.25  # steps on a line: up to 0.6 column widths forward, 0.35 back
LAND = 0.2  # a return sweep leaves the last fifth of a line and lands in the first fifth of the next
OUTLIER = 0.02
GAP_S = 3.0  # after this long without a fixation, the eyes may be anywhere
DRIFT = 0.03  # chance per 0.3 s that the offset moves one grid step either way
MIX = 0.002  # share of the prior returned each fixation, so no hypothesis is ever ruled out
GRID_SD, GRID_STEP = 3.0, 0.2  # offset grid: +-3 SD in steps of 0.2 SD
SCATTER, OFFSET = 0.9, 1.2  # per-fixation scatter and offset spread, as shares of the per-axis gaze SD


@dataclass
class _Line:
    key: Key
    box: LineBox
    band: tuple[float, float]  # vertical extent owned by the line: (top, height), page px
    column: float  # width of the widest line in its column
    first: bool  # first line of its block
    next: int | None = None  # index in PageReader.lines of the line below, in the same column
    prev: int | None = None
    start: int = 0  # index of its block's first line


class PageReader:
    """The line being read on one page, updated one fixation at a time."""

    def __init__(self, doc: str, sigma: float, offset_sd: float) -> None:
        self.doc = doc
        self.spread = 0.0  # the per-axis gaze SD this was made for, CSS px (set by the caller)
        self.sigma = sigma  # scatter of one fixation around where the eyes are, offset removed, CSS px
        self.offset_sd = offset_sd  # spread of the slowly moving offset, CSS px
        self.sigma_x = math.hypot(sigma, offset_sd)  # no horizontal offset is modelled: widen instead
        n = round(GRID_SD / GRID_STEP)
        self.offsets = [k * GRID_STEP * offset_sd for k in range(-n, n + 1)]
        prior = [math.exp(-0.5 * (k * GRID_STEP) ** 2) for k in range(-n, n + 1)]
        self.prior = [p / sum(prior) for p in prior]
        self.blocks: dict[str, list[LineBox]] = {}
        self.lines: list[_Line] = []
        self.index: dict[Key, int] = {}
        self.tops: list[float] = []
        self.dirty = False
        self.alpha: dict[Key | None, list[float]] = {}
        self.last: tuple[float, float] | None = None  # previous fixation (x, t)

    # -- the page ----------------------------------------------------------------------------------

    def set_block(self, block: str, lines: Sequence[LineBox]) -> None:
        """A block's lines, new or re-measured (its wrapping changed: what was decoded on it goes)."""
        self.blocks[block] = list(lines)
        for key in [k for k in self.alpha if k is not None and k[0] == block]:
            del self.alpha[key]
        self.dirty = True

    def _build(self) -> None:
        lines: list[_Line] = []
        for block, boxes in self.blocks.items():
            centers = [b.y + b.h / 2 for b in boxes]
            gaps = sorted(b - a for a, b in zip(centers, centers[1:], strict=False) if b > a)
            pitch = gaps[len(gaps) // 2] if gaps else None
            for i, b in enumerate(boxes):
                pad = max(((pitch or 1.45 * b.h) - b.h) / 2, 0.0)
                lines.append(_Line((block, i), b, (b.y - pad, b.h + 2 * pad), b.w, i == 0))
        lines.sort(key=lambda line: (line.box.y, line.box.x))
        self.lines, self.index = lines, {line.key: i for i, line in enumerate(lines)}
        self.tops = [line.band[0] for line in lines]
        for i, line in enumerate(lines):
            line.start = self.index.get((line.key[0], 0), i)
            a = line.box
            # The widest line nearby that shares the column, and the nearest line below in it.
            for j in range(max(0, i - 40), min(len(lines), i + 40)):
                b = lines[j].box
                if min(a.x + a.w, b.x + b.w) - max(a.x, b.x) >= 0.3 * min(a.w, b.w):
                    line.column = max(line.column, b.w)
                    if j > i and line.next is None and b.y > a.y + 0.5 * a.h:
                        line.next = j
            if line.next is not None and lines[line.next].prev is None:
                lines[line.next].prev = i
        self.dirty = False

    def _visible(self, top: float, bottom: float) -> list[int]:
        # Bands are sorted by top; a band is at most a few lines tall, so look a little above.
        lo = bisect_left(self.tops, top - 200)
        hi = bisect_right(self.tops, bottom)
        return [i for i in range(lo, hi) if self.lines[i].band[0] + self.lines[i].band[1] > top]

    # -- gaze --------------------------------------------------------------------------------------

    def update(self, x: float, y: float, t: float, view: tuple[float, float, float]) -> dict[Key | None, float]:
        """Add a fixation at page point (x, y), CSS px, t in seconds; ``view`` is the viewport's
        (top, bottom, width) in page px. Returns P(line) for each line in view and for OFF."""
        if self.dirty:
            self._build()
        active = self._visible(view[0], view[1])
        G = len(self.offsets)
        weights = [FIRST if self.lines[i].first else 1.0 for i in active]
        total = sum(weights) or 1.0
        land = {self.lines[i].key: w / total for i, w in zip(active, weights, strict=True)}  # where jumps land
        if not self.alpha or self.last is None:
            self.alpha = {key: [p * q for q in self.prior] for key, p in land.items()}
            self.alpha[OFF] = [0.0] * G
        else:
            self.alpha = self._step(x - self.last[0], t - self.last[1], active, land, view[2])
        self._emit(x, y, active, view)
        self.last = (x, t)
        return {key: sum(row) for key, row in self.alpha.items()}

    def _step(
        self, dx: float, dt: float, active: list[int], land: dict[Key, float], width: float
    ) -> dict[Key | None, list[float]]:
        G = len(self.offsets)
        new: dict[Key | None, list[float]] = {self.lines[i].key: [0.0] * G for i in active}
        new[OFF] = [0.0] * G
        anywhere = 1.0 / (2 * max(width, 1.0))  # density of a step to an unrelated place
        noise = self.sigma * SQRT2
        broad = dt > GAP_S
        spread = [0.0] * G  # mass that may land anywhere (jumps), per offset

        def add(key: Key | None, row: list[float], p: float) -> None:
            target = new.get(key)
            if target is not None and p > 0:
                for k in range(G):
                    target[k] += row[k] * p

        for key, row in self.alpha.items():
            if key is OFF:
                add(OFF, row, OFF_STAY * (1.0 if broad else anywhere))
                p = (1 - OFF_STAY) * (1.0 if broad else anywhere)
                for k in range(G):
                    spread[k] += row[k] * p
                continue
            i = self.index.get(key)
            if i is None:
                continue
            line = self.lines[i]
            if broad:  # back after a while: most likely where they were
                add(key, row, 0.5)
                add(OFF, row, 0.05)
                for k in range(G):
                    spread[k] += row[k] * 0.45
                continue
            w = line.column
            stay = (1 - BACK_SHARE) * _inside(dx, -0.05 * w, (FORWARD + 0.05) * w, noise) / ((FORWARD + 0.05) * w)
            stay += BACK_SHARE * _inside(dx, -BACK * w, BACK * w, noise) / (BACK * w)
            add(key, row, STAY * stay)
            add(OFF, row, TO_OFF * anywhere)
            if line.next is not None:
                below = self.lines[line.next]
                lo = below.box.x - (line.box.x + line.box.w)
                size = LAND * min(below.box.w, w) + LAND * min(line.box.w, w)
                add(below.key, row, NEXT * _inside(dx, lo, size, noise) / max(size, 1.0))
                if below.next is not None:
                    add(self.lines[below.next].key, row, NEXT2 * anywhere)
            if line.prev is not None:
                above = self.lines[line.prev]
                add(above.key, row, PREV * anywhere)
                if above.prev is not None:
                    add(self.lines[above.prev].key, row, PREV2 * anywhere)
            if line.start != i:
                add(self.lines[line.start].key, row, START * anywhere)
            for k in range(G):
                spread[k] += row[k] * JUMP * anywhere
        for key, p in land.items():
            target = new[key]
            for k in range(G):
                target[k] += spread[k] * p
        # The offset drifts: a step to a neighbouring grid point now and then.
        q = min(0.25, DRIFT * max(dt, 0.05) / 0.3)
        for key, row in new.items():
            moved = [(1 - 2 * q) * v for v in row]
            for k in range(G):
                moved[k - 1 if k else k] += q * row[k]
                moved[k + 1 if k < G - 1 else k] += q * row[k]
            new[key] = moved
        return new

    def _emit(self, x: float, y: float, active: list[int], view: tuple[float, float, float]) -> None:
        G = len(self.offsets)
        top, bottom, width = view
        uniform = 1.0 / max(width * (bottom - top), 1.0)  # off the text: anywhere on the screen
        total = 0.0
        for key, row in self.alpha.items():
            if key is OFF:
                for k in range(G):
                    row[k] *= uniform
                total += sum(row)
                continue
            line = self.lines[self.index[key]]
            # Horizontally: inside the line's extent, at the density of anywhere in its column, so
            # a short line is not favoured for the fixations it can hold.
            across = _inside(x, line.box.x, line.box.w, self.sigma_x) / line.column
            band_top, band_h = line.band
            for k in range(G):
                down = _inside(y - self.offsets[k], band_top, band_h, self.sigma) / band_h
                row[k] *= (1 - OUTLIER) * down * across + OUTLIER * uniform
            total += sum(row)
        if total <= 0 or not math.isfinite(total):  # nothing fits: start over from the prior
            self.alpha = {}
            self.last = None
            weights = {self.lines[i].key: (FIRST if self.lines[i].first else 1.0) for i in active}
            s = sum(weights.values()) or 1.0
            self.alpha = {key: [w / s * q for q in self.prior] for key, w in weights.items()}
            self.alpha[OFF] = [0.0] * G
            return
        land_total = sum(FIRST if self.lines[self.index[k]].first else 1.0 for k in self.alpha if k is not OFF) or 1.0
        for key, row in self.alpha.items():
            back = 0.0 if key is OFF else (FIRST if self.lines[self.index[key]].first else 1.0) / land_total
            for k in range(G):
                row[k] = (1 - MIX) * row[k] / total + MIX * back * self.prior[k]

    def best(self) -> tuple[Key | None, float]:
        """The likeliest line (OFF when off the text is likeliest) and its probability."""
        marginal = {key: sum(row) for key, row in self.alpha.items()}
        if not marginal:
            return OFF, 0.0
        key = max(marginal, key=marginal.__getitem__)
        return key, marginal[key]

    def offset(self) -> float:
        """The decoder's estimate of the tracker's vertical offset, CSS px."""
        return sum(row[k] * b for row in self.alpha.values() for k, b in enumerate(self.offsets))

    def text(self, key: Key) -> str:
        return self.blocks[key[0]][key[1]].text


@dataclass
class Visit:
    """The eyes on one block, from the first fixation decoded there to the last: what was read on
    it, line by line, for ``reading.jsonl``."""

    doc: str
    block: str
    line_count: int
    t0: float  # epoch seconds
    texts: list[str] = field(default_factory=list)  # the text of each line
    tab: int | None = None
    url: str | None = None
    title: str | None = None
    t1: float = 0.0
    fixations: int = 0
    seconds: dict[int, float] = field(default_factory=dict)  # attended time per line
    path: list[list[float]] = field(default_factory=list)  # [seconds since t0, line] at each change
    offset_px: float = 0.0
    p_sum: float = 0.0  # the decoder's probability for each fixation's line, summed

    def add(self, line: int, t: float, t_end: float, duration: float, p: float = 1.0) -> None:
        self.p_sum += p
        self.seconds[line] = self.seconds.get(line, 0.0) + duration
        if not self.path or self.path[-1][1] != line:
            self.path.append([round(t - self.t0, 2), line])
        self.t1, self.fixations = t_end, self.fixations + 1

    def summary(self) -> dict:
        # A pass is a stay on a line of at least 0.4 s; shorter ones are mostly decoding noise.
        passes: dict[int, int] = {}
        for k, (t, line) in enumerate(self.path):
            end = self.path[k + 1][0] if k + 1 < len(self.path) else self.t1 - self.t0
            if end - t >= 0.4:
                passes[int(line)] = passes.get(int(line), 0) + 1
        return {
            "line_count": self.line_count,
            "lines": [
                {
                    "line": i,
                    "text": self.texts[i] if i < len(self.texts) else "",
                    "seconds": round(s, 2),
                    "passes": max(passes.get(i, 0), 1),
                }
                for i, s in sorted(self.seconds.items())
                if s >= 0.1
            ],
            "coverage": round(sum(s >= 0.25 for s in self.seconds.values()) / max(self.line_count, 1), 2),
            "path": self.path,
            "offset_px": round(self.offset_px, 1),
            "p": round(self.p_sum / max(self.fixations, 1), 2),  # mean confidence in the lines decoded
        }

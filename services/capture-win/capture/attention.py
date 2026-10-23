"""Live attention: which text block the eyes are on, and when they stay on one too long.

The extension reports the page in front: heartbeats and snapshots of the visible text blocks,
with where the viewport sits on the monitor. The eye tracker reports fixations as fractions of
the same monitor. Each fixation is placed on the page, then on the nearest block, and
consecutive fixations on one block form a run. A brief look away (``excursion_s``) does not end
a run; a longer one, a gap in gaze (``max_gap_s``), a tab switch or a navigation does.

A run becomes a *dwell* when its fixation time passes both a floor (``threshold_s``) and
``factor`` times the time the block's text takes to read at ``wpm``. Reading a long paragraph at
normal speed is therefore not a dwell; reading it at under half speed, re-reading it, or staring
at a short line is. Average adult silent reading of English non-fiction is about 238 words a
minute (Brysbaert 2019, a meta-analysis of 190 studies).

Webcam gaze is accurate to roughly 3-4 degrees, about 150 px at 60 cm from a 24" monitor: enough
to tell paragraphs apart, not words.
"""

from __future__ import annotations

import math
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from .records import Block, Blocks, Fixation, Geometry, Record, View

TEXT_CACHE = 20_000


@dataclass(frozen=True)
class Hit:
    tab: int
    doc: str
    x: float  # CSS px in the viewport
    y: float
    block: str | None


@dataclass
class Front:
    tab: int
    url: str
    title: str
    doc: str
    geometry: Geometry
    blocks: list[Block]
    blocks_scroll: float  # scroll position when the blocks were measured
    seen: datetime


@dataclass
class Run:
    id: str
    tab: int
    url: str
    title: str
    doc: str
    block: str
    t0: datetime
    t1: datetime
    dwell: float
    fixations: int
    fired: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return self.doc, self.block


class Attention:
    def __init__(
        self,
        *,
        threshold_s: float = 8.0,
        factor: float = 2.0,
        wpm: float = 238.0,
        margin_px: float = 40.0,
        max_gap_s: float = 2.0,
        excursion_s: float = 1.0,
        view_ttl_s: float = 12.0,
    ) -> None:
        self.threshold_s = threshold_s
        self.factor = factor
        self.wpm = wpm
        self.margin_px = margin_px
        self.max_gap_s = max_gap_s
        self.excursion_s = excursion_s
        self.view_ttl_s = view_ttl_s
        self.front: Front | None = None
        self.monitor: tuple[int, int] | None = None
        self.run: Run | None = None
        self.away: datetime | None = None  # when the eyes left the run's block
        self.texts: OrderedDict[tuple[str, str], tuple[str, str, int]] = OrderedDict()  # tag, text, words

    # -- the page in front -------------------------------------------------------------------------

    def browser(self, record: Record) -> list[dict]:
        """Follow the browser; returns dwell records that the change ended."""
        kind = getattr(record, "kind", None)
        if isinstance(record, Blocks | View):
            ended = self.end() if self.front and self.front.doc != record.doc else []
            if isinstance(record, Blocks):
                for block in record.blocks:
                    if block.text is not None:
                        self._remember(record.doc, block)
                blocks, measured = record.blocks, record.geometry.scroll_y
            elif self.front and self.front.doc == record.doc:
                blocks, measured = self.front.blocks, self.front.blocks_scroll
            else:
                blocks, measured = [], record.geometry.scroll_y
            self.front = Front(
                record.tab, record.url, record.title, record.doc, record.geometry, blocks, measured, record.t
            )
            return ended
        leaves = (
            (kind == "focus" and (self.front is None or record.tab != self.front.tab))
            or (kind == "page" and self.front is not None and record.tab == self.front.tab)
            or (kind == "idle" and record.state == "locked")
        )
        if leaves:
            self.front = None
            return self.end()
        return []

    def gaze_monitor(self, monitor: list[int] | None) -> None:
        self.monitor = (monitor[0], monitor[1]) if monitor else None

    def _remember(self, doc: str, block: Block) -> None:
        text = block.text or ""
        self.texts[(doc, block.id)] = (block.tag, text, len(text.split()) if block.words is None else block.words)
        self.texts.move_to_end((doc, block.id))
        while len(self.texts) > TEXT_CACHE:
            self.texts.popitem(last=False)

    # -- gaze --------------------------------------------------------------------------------------

    def place(self, fixation: Fixation) -> Hit | None:
        """The page point and text block under a fixation, if the page in front is under it."""
        front, monitor = self.front, self.monitor
        if front is None or monitor is None:
            return None
        if abs((fixation.t - front.seen).total_seconds()) > self.view_ttl_s:
            return None
        g = front.geometry
        if not g.primary or abs(monitor[0] / monitor[1] - g.screen_w / g.screen_h) > 0.02:
            return None
        x = (fixation.x * g.screen_w - g.vx) / g.scale
        y = (fixation.y * g.screen_h - g.vy) / g.scale
        if not (0 <= x <= g.inner_w and 0 <= y <= g.inner_h):
            return None
        shift = g.scroll_y - front.blocks_scroll
        best: tuple[float, float, str] | None = None
        for block in front.blocks:
            left, top, width, height = block.rect
            top -= shift
            dx = max(left - x, 0.0, x - left - width)
            dy = max(top - y, 0.0, y - top - height)
            candidate = (math.hypot(dx, dy), width * height, block.id)
            if candidate[0] <= self.margin_px and (best is None or candidate < best):
                best = candidate
        return Hit(front.tab, front.doc, round(x, 1), round(y, 1), best[2] if best else None)

    def fixation(self, fixation: Fixation) -> tuple[Hit | None, list[dict]]:
        """Place one fixation and advance the run; returns the hit and any dwell records."""
        hit = self.place(fixation)
        events = self.advance(fixation.t)
        duration = (fixation.t_end - fixation.t).total_seconds()
        key = (hit.doc, hit.block) if hit and hit.block else None
        run = self.run
        if run is not None:
            if key == run.key:
                run.t1, run.dwell, run.fixations = fixation.t_end, run.dwell + duration, run.fixations + 1
                self.away = None
            else:
                self.away = self.away or fixation.t
                if (fixation.t_end - self.away).total_seconds() > self.excursion_s:
                    events += self.end()
        if self.run is None and key is not None and self.front is not None:
            front = self.front
            self.run = Run(
                uuid.uuid4().hex[:16], front.tab, front.url, front.title, front.doc, key[1],
                fixation.t, fixation.t_end, duration, 1,
            )  # fmt: skip
        run = self.run
        if run is not None and not run.fired and run.dwell >= self.limit(run):
            run.fired = True
            events.append(self._record(run, "start"))
        return hit, events

    def advance(self, now: datetime) -> list[dict]:
        """End the run if gaze has been absent for longer than ``max_gap_s``."""
        if self.run is not None and (now - self.run.t1).total_seconds() > self.max_gap_s:
            return self.end()
        return []

    def end(self) -> list[dict]:
        run, self.run, self.away = self.run, None, None
        return [self._record(run, "end")] if run is not None and run.fired else []

    # -- dwell -------------------------------------------------------------------------------------

    def reading_s(self, run: Run) -> float | None:
        """Seconds the block's text takes to read at ``wpm``; None when its text is unknown."""
        entry = self.texts.get(run.key)
        return entry[2] / self.wpm * 60 if entry else None

    def limit(self, run: Run) -> float:
        reading = self.reading_s(run)
        return max(self.threshold_s, self.factor * reading) if reading is not None else self.threshold_s

    def _record(self, run: Run, phase: str) -> dict:
        tag, text, _ = self.texts.get(run.key, (None, None, None))
        reading = self.reading_s(run)
        return {
            "id": f"{run.id}-{phase}",
            "kind": "dwell",
            "dwell": run.id,
            "phase": phase,
            "t": run.t0.isoformat(),
            "t_end": run.t1.isoformat(),
            "dwell_s": round(run.dwell, 2),
            "reading_s": None if reading is None else round(reading, 2),
            "fixations": run.fixations,
            "tab": run.tab,
            "url": run.url,
            "title": run.title,
            "doc": run.doc,
            "block": run.block,
            "tag": tag,
            "text": text,
        }

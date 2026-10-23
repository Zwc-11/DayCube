"""Live attention: which text block the eyes are on, and when they stay on one too long.

The extension reports the page in front: heartbeats and snapshots of the visible text blocks,
with where the viewport sits on the monitor. The eye tracker reports fixations as fractions of
the same monitor. Each fixation is placed on the page.

Which block a fixation belongs to is decided by a filter, not by the rectangle under it. Webcam
gaze is off by more than the gap between two paragraphs: a 69 px median error on a 2560 px monitor
is 28 CSS px at 125% scaling and 100% zoom, and on example.com the paragraph sits 16-22 px from its
neighbours. Counting only fixations that land inside the paragraph's rectangle, with a run ended by
the first second outside it, made a 20 s stare count for 1-5 s on the 2026-10-23 Windows check.

The filter is a sticky hidden Markov model over "on block b" for each visible block and "on
nothing" (images, video, margins, the toolbar):

- emission: the chance the tracker reports this point if the eyes are anywhere in the block, which
  is the block's rectangle blurred by the gaze error (a Gaussian with the spread the error implies:
  from clicks once enough have been seen, see ``anchors.py``, else from the calibration), mixed with
  a small uniform share for tracker outliers. "On nothing" is uniform over the screen. Webcam error
  is mostly a slowly moving offset, so consecutive fixations are not independent evidence; each
  likelihood is raised to ``evidence`` (below one) so a run of them is not over-believed.
- transition: the eyes stay where they were with probability ``exp(-rate * dt)``; otherwise they
  move, to nothing with probability ``nothing`` and to a block in proportion to its words. Once a
  run has lasted its block's reading time, the eyes are more likely to have moved on (the rate
  rises by ``restless``): otherwise a tracker biased upward keeps crediting a paragraph while the
  reader goes on to the next one.

Filtering forward gives, after each fixation, the probability that the eyes are on each block. A
*run* starts when one block leads with a posterior of at least ``lead`` and lasts while its block
leads or stays plausible (``keep``). A longer look elsewhere (``excursion_s``), a gap in gaze
(``max_gap_s``), a tab switch or a navigation ends it. A run's dwell is the attended time the
filter credits to its block: each fixation's duration, plus the short gap before it (a blink or a
saccade, at most ``bridge_s``), in full while the block leads and weighted by its posterior while
it is only plausible.

A run becomes a *dwell* when its time passes both a floor (``threshold_s``) and ``factor`` times
the time the block's text takes to read at ``wpm``. Reading a long paragraph at normal speed is
therefore not a dwell; reading it at under half speed, re-reading it, or staring at a short line
is. Average adult silent reading of English non-fiction is about 238 words a minute (Brysbaert
2019, a meta-analysis of 190 studies). A dwell also needs a block the tracker can single out at its
current error (``resolve``): when the error is larger than the space between blocks, a dwell on a
heading or a one-line paragraph would be a guess between it and its neighbours.

The parameters were chosen with ``eval/gaze_sim.py``, which runs this, with the tracker's own
fixation detector, against simulated webcam gaze fitted to the calibration error the tracker
reports (results in ``docs/capture.md``).
"""

from __future__ import annotations

import math
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from .anchors import RAYLEIGH_MEDIAN, Anchors
from .records import Block, Blocks, Click, Fixation, GazeState, Geometry, Record, View

TEXT_CACHE = 20_000
NOTHING = ""  # the "on no text block" state; block ids are never empty
DEFAULT_ERROR_PX = 90.0  # a typical webcam calibration, when the tracker has not said
SQRT2 = math.sqrt(2)


@dataclass(frozen=True)
class Hit:
    tab: int
    doc: str
    x: float  # CSS px in the viewport, after the click-based correction
    y: float
    block: str | None  # the leading block, when it leads with at least ``lead``
    p: float | None = None  # its posterior


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


def _between(a: float, b: float) -> float:
    """P(b < Z < a) for a standard normal scaled by 1/sqrt(2) (erf units), accurate in the tails."""
    if b > 0:
        return 0.5 * (math.erfc(b) - math.erfc(a))
    if a < 0:
        return 0.5 * (math.erfc(-a) - math.erfc(-b))
    return 0.5 * (math.erf(a) - math.erf(b))


def _box(v: float, lo: float, size: float, sigma: float) -> float:
    """Density at ``v`` of a point uniform on [lo, lo + size] plus Gaussian error ``sigma``."""
    size = max(size, 1.0)
    k = sigma * SQRT2
    return _between((v - lo) / k, (v - lo - size) / k) / size


class Attention:
    def __init__(
        self,
        *,
        threshold_s: float = 8.0,
        factor: float = 2.0,
        wpm: float = 238.0,
        max_gap_s: float = 2.0,
        excursion_s: float = 2.5,
        view_ttl_s: float = 12.0,
        rate: float = 0.3,
        nothing: float = 0.2,
        outlier: float = 0.01,
        lead: float = 0.5,
        keep: float = 0.25,
        resolve: float = 0.5,
        restless: float = 2.0,
        bridge_s: float = 0.4,
        base_words: float = 10.0,
        evidence: float = 0.5,
        anchors: Anchors | None = None,
    ) -> None:
        self.threshold_s = threshold_s
        self.factor = factor
        self.wpm = wpm
        self.max_gap_s = max_gap_s
        self.excursion_s = excursion_s
        self.view_ttl_s = view_ttl_s
        self.rate, self.nothing, self.outlier, self.lead, self.bridge_s = rate, nothing, outlier, lead, bridge_s
        self.base_words, self.evidence, self.keep, self.resolve = base_words, evidence, keep, resolve
        self.restless = restless
        self.anchors = anchors or Anchors()
        self.front: Front | None = None
        self.monitor: tuple[int, int] | None = None
        self.calibrated_px: float | None = None
        self.run: Run | None = None
        self.away: datetime | None = None  # when the eyes left the run's block
        self.belief: dict[str, float] = {}  # posterior over the front page's blocks and NOTHING
        self.belief_doc: str | None = None
        self.last_t: datetime | None = None  # start of the last fixation the filter took
        self.last_end: datetime | None = None  # end of the last fixation, for bridging blinks
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
        if isinstance(record, Click):
            self._click(record)
            return []
        leaves = (
            (kind == "focus" and (self.front is None or record.tab != self.front.tab))
            # A same-document URL change (pushState, scroll-spy pages) keeps the page; new
            # content shows up in the next snapshot.
            or (
                kind == "page"
                and self.front is not None
                and record.tab == self.front.tab
                and "history_state" not in record.qualifiers
            )
            or (kind == "idle" and record.state == "locked")
        )
        if leaves:
            self.front = None
            self.belief, self.belief_doc = {}, None
            return self.end()
        return []

    def gaze_state(self, state: GazeState) -> None:
        """The tracker's report: which monitor it is calibrated on and how well."""
        self.gaze_monitor(state.monitor)
        self.calibrated_px = state.error_px
        self.anchors.set_monitor(self.monitor, state.error_px)

    def gaze_monitor(self, monitor: list[int] | None) -> None:
        self.monitor = (monitor[0], monitor[1]) if monitor else None
        self.anchors.set_monitor(self.monitor, self.calibrated_px)

    def _click(self, click: Click) -> None:
        """A click with its position becomes a calibration point (``anchors.py``)."""
        g = click.geometry
        if click.x is None or click.y is None or g is None or not g.primary:
            return
        if self.monitor is not None and abs(self.monitor[0] / self.monitor[1] - g.screen_w / g.screen_h) > 0.02:
            return
        fx = (g.vx + click.x * g.scale) / g.screen_w
        fy = (g.vy + click.y * g.scale) / g.screen_h
        self.anchors.click(click.t.timestamp(), fx, fy)

    def _remember(self, doc: str, block: Block) -> None:
        self.remember(doc, block.id, block.tag, block.text or "", block.words)

    def remember(self, doc: str, block: str, tag: str, text: str, words: int | None = None) -> None:
        self.texts[(doc, block)] = (tag, text, len(text.split()) if words is None else words)
        self.texts.move_to_end((doc, block))
        while len(self.texts) > TEXT_CACHE:
            self.texts.popitem(last=False)

    # -- gaze --------------------------------------------------------------------------------------

    def error_px(self, now: float) -> float:
        """The median gaze error to allow for, in monitor pixels: measured from clicks when there
        are enough, else the calibration's held-out error."""
        return self.anchors.error_px(now) or self.calibrated_px or DEFAULT_ERROR_PX

    def _locate(self, fixation: Fixation) -> tuple[Front, float, float] | None:
        """The fixation as a viewport point (CSS px), corrected by clicks, if the page in front is
        on the tracker's monitor."""
        front, monitor = self.front, self.monitor
        if front is None or monitor is None:
            return None
        if abs((fixation.t - front.seen).total_seconds()) > self.view_ttl_s:
            return None
        g = front.geometry
        if not g.primary or abs(monitor[0] / monitor[1] - g.screen_w / g.screen_h) > 0.02:
            return None
        dx, dy = self.anchors.offset(fixation.x, fixation.y, fixation.t.timestamp())
        x = ((fixation.x - dx) * g.screen_w - g.vx) / g.scale
        y = ((fixation.y - dy) * g.screen_h - g.vy) / g.scale
        return front, x, y

    def _likelihoods(self, front: Front, x: float, y: float, now: float) -> dict[str, float]:
        g = front.geometry
        sigma = self.error_px(now) / RAYLEIGH_MEDIAN * (g.screen_w / self.monitor[0]) / g.scale
        uniform = (g.scale * g.scale) / (g.screen_w * g.screen_h)  # 1 / screen area in CSS px
        shift = g.scroll_y - front.blocks_scroll
        out = {NOTHING: uniform}
        for block in front.blocks:
            left, top, width, height = block.rect
            inside = _box(x, left, width, sigma) * _box(y, top - shift, height, sigma)
            out[block.id] = (1 - self.outlier) * inside + self.outlier * uniform
        return out

    def place(self, fixation: Fixation) -> Hit | None:
        """Where one fixation lands taken alone (no memory of earlier ones): the page point, and
        the block it most likely belongs to, if one is likely enough."""
        located = self._locate(fixation)
        if located is None:
            return None
        front, x, y = located
        g = front.geometry
        if not (0 <= x <= g.inner_w and 0 <= y <= g.inner_h):
            return None
        like = self._likelihoods(front, x, y, fixation.t.timestamp())
        prior = self._prior(front)
        posterior = self._normalize({k: prior[k] * v for k, v in like.items()})
        best = max((k for k in posterior if k != NOTHING), key=posterior.get, default=None)
        block = best if best is not None and posterior[best] >= self.lead else None
        return Hit(
            front.tab, front.doc, round(x, 1), round(y, 1), block, None if block is None else round(posterior[block], 2)
        )

    def _prior(self, front: Front) -> dict[str, float]:
        """Where the eyes go when they move: to nothing with probability ``nothing``, else to a
        block in proportion to its words plus ``base_words``. People spend time on text in
        proportion to its length; the base keeps a two-word heading from being negligible. This
        matters only when the gaze error is too large to tell neighbouring blocks apart: a
        heading and a paragraph then look alike to the tracker, and the paragraph is likelier."""
        if not front.blocks:
            return {NOTHING: 1.0}
        known = [self.texts[(front.doc, b.id)][2] for b in front.blocks if (front.doc, b.id) in self.texts]
        typical = sorted(known)[len(known) // 2] if known else 0
        weights = {}
        for b in front.blocks:
            entry = self.texts.get((front.doc, b.id))
            weights[b.id] = (entry[2] if entry else typical) + self.base_words
        total = sum(weights.values())
        return {NOTHING: self.nothing} | {k: (1 - self.nothing) * w / total for k, w in weights.items()}

    @staticmethod
    def _normalize(values: dict[str, float]) -> dict[str, float]:
        total = sum(values.values())
        if total <= 0 or not math.isfinite(total):
            return {k: 1 / len(values) for k in values}
        return {k: v / total for k, v in values.items()}

    def _filter(self, front: Front, x: float, y: float, t: datetime) -> dict[str, float]:
        """One step of the forward filter; returns the posterior over blocks and NOTHING."""
        if self.belief_doc != front.doc:
            self.belief, self.belief_doc, self.last_t = {}, front.doc, None
        like = self._likelihoods(front, x, y, t.timestamp())
        jump = self._prior(front)
        if self.belief and self.last_t is not None:
            dt = max((t - self.last_t).total_seconds(), 0.0)
            kept = sum(self.belief.get(k, 0.0) for k in like) or 1.0  # blocks that scrolled away drop out
            stays = dict.fromkeys(like, math.exp(-self.rate * dt))
            run = self.run
            if self.restless and run is not None and run.doc == front.doc and run.block in stays:
                reading = self.reading_s(run)
                if reading is not None and run.dwell > reading:
                    # Past its reading time, the eyes are likelier to have moved on than at first.
                    stays[run.block] = math.exp(-self.rate * (1 + self.restless) * dt)
            moving = sum((1 - stays[k]) * self.belief.get(k, 0.0) / kept for k in like)
            predicted = {k: stays[k] * self.belief.get(k, 0.0) / kept + moving * jump[k] for k in like}
        else:
            predicted = jump
        self.belief = self._normalize({k: predicted[k] * like[k] ** self.evidence for k in like})
        self.last_t = t
        return self.belief

    def fixation(self, fixation: Fixation) -> tuple[Hit | None, list[dict]]:
        """Place one fixation and advance the run; returns the hit and any dwell records."""
        located = self._locate(fixation)
        events = self.advance(fixation.t)
        duration = (fixation.t_end - fixation.t).total_seconds()
        if self.last_end is not None:
            gap = (fixation.t - self.last_end).total_seconds()
            if 0 < gap <= self.bridge_s:
                duration += gap  # a blink or a saccade between two looks belongs to the looking
        self.last_end = fixation.t_end
        posterior: dict[str, float] = {}
        hit = None
        if located is not None:
            front, x, y = located
            posterior = self._filter(front, x, y, fixation.t)
            best = max((k for k in posterior if k != NOTHING), key=posterior.get, default=None)
            leads = best is not None and posterior[best] >= self.lead
            g = front.geometry
            if 0 <= x <= g.inner_w and 0 <= y <= g.inner_h:
                hit = Hit(
                    front.tab, front.doc, round(x, 1), round(y, 1), best if leads else None,
                    round(posterior[best], 2) if leads else None,
                )  # fmt: skip
            key = (front.doc, best) if leads else None
        else:
            key = None
        run = self.run
        if run is not None:
            share = posterior.get(run.block, 0.0) if located is not None and located[0].doc == run.doc else 0.0
            if key == run.key or share >= self.keep:
                # Full time while the block leads; while it is only plausible, its share.
                run.dwell += duration * (1.0 if key == run.key else share)
                run.t1, run.fixations = fixation.t_end, run.fixations + (key == run.key)
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
        if self.monitor is not None:
            self.anchors.fixation(fixation.t.timestamp(), fixation.t_end.timestamp(), fixation.x, fixation.y)
        run = self.run
        if run is not None and not run.fired and run.dwell >= self.limit(run) and self._resolvable(run):
            run.fired = True
            events.append(self._record(run, "start"))
        return hit, events

    def _resolvable(self, run: Run) -> bool:
        """Whether the tracker, at its current error, can single out the run's block: a look at
        its middle would make it the likeliest block by at least ``resolve``. When the error is
        larger than the space between blocks, time near a small block (a heading, a one-line
        paragraph) is mostly time on its neighbours, and a dwell on it would be a guess."""
        front = self.front
        if self.resolve <= 0 or front is None or front.doc != run.doc:
            return True
        block = next((b for b in front.blocks if b.id == run.block), None)
        if block is None:
            return False
        left, top, width, height = block.rect
        shift = front.geometry.scroll_y - front.blocks_scroll
        like = self._likelihoods(front, left + width / 2, top - shift + height / 2, run.t1.timestamp())
        prior = self._prior(front)
        posterior = self._normalize({k: prior[k] * v for k, v in like.items()})
        return posterior.get(run.block, 0.0) >= self.resolve

    def advance(self, now: datetime) -> list[dict]:
        """End the run if gaze has been absent for longer than ``max_gap_s``."""
        if self.run is not None and (now - self.run.t1).total_seconds() > self.max_gap_s:
            return self.end()
        return []

    def end(self) -> list[dict]:
        run, self.run, self.away = self.run, None, None
        return [self._record(run, "end")] if run is not None and run.fired else []

    def health(self, now: float) -> dict:
        """How well gaze is placed now, for /status."""
        error = self.anchors.error_px(now)
        return {
            **self.anchors.health(now),
            "calibrated_error_px": self.calibrated_px,
            "planned_error_px": round(error or self.calibrated_px or DEFAULT_ERROR_PX, 1),
        }

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

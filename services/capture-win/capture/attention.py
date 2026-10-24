"""Live attention: which text block the eyes are on, and when they stay on one too long.

The extension reports the page each browser window shows, whether or not the window has focus
(a page read beside a terminal is on screen too): heartbeats and snapshots of the visible text
blocks, with where the viewport sits on the monitor, and a notice when the page leaves the
screen. The eye tracker reports fixations as fractions of the same monitor. Each fixation is
placed on the page whose window it falls in.

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

Each fixation also goes to the line decoder (``reading.py``), which follows reading line by line
over the whole page, independently of the runs; the hit carries the line it decodes. A *visit* is
the decoder's stay on one block: when the eyes move on (two fixations decoded on another block),
the page changes, the block is re-measured or gaze stops for ``VISIT_GAP_S``, the visit becomes a
``reading.jsonl`` record of the lines read, with the time on each.

The parameters were chosen with ``eval/gaze_sim.py``, which runs this, with the tracker's own
fixation detector, against simulated webcam gaze fitted to the calibration error the tracker
reports (results in ``docs/capture.md``).
"""

from __future__ import annotations

import math
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from .anchors import RAYLEIGH_MEDIAN, Anchors
from .reading import OFF, OFFSET, SCATTER, LineBox, PageReader, Visit
from .records import Block, Blocks, Click, Fixation, GazeState, Geometry, Record, View

TEXT_CACHE = 20_000
LAYOUT_CACHE = 5_000
VISIT_GAP_S = 10.0  # a block visit ends after this long without a fixation decoded on the page
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
    # The line being read, from the line decoder (``reading.py``), which follows reading across
    # blocks on its own: its block can differ from ``block`` near a paragraph's edge.
    line_block: str | None = None
    line: int | None = None  # 0 = the block's first line
    line_p: float | None = None


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
    window: int | None = None  # the browser window showing it


# Why a fixation was or was not placed on a page, for /status and the doctor.
NO_PAGE, STALE, OTHER_MONITOR, OUTSIDE, ON_PAGE, ON_LINE = range(6)


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
        restless: float = 3.0,
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
        # The page each browser window shows (a window that is not focused still shows its page,
        # beside another app or another window), and the one gaze was last placed on.
        self.fronts: dict[int | None, Front] = {}
        self.front: Front | None = None
        self.focused: int | None = None  # the focused browser window; None when another app has focus
        self.monitor: tuple[int, int] | None = None
        self.calibrated_px: float | None = None
        self.run: Run | None = None
        self.away: datetime | None = None  # when the eyes left the run's block
        self.challenger: Run | None = None  # the block leading while the eyes are away from the run's
        self.belief: dict[str, float] = {}  # posterior over the front page's blocks and NOTHING
        self.belief_doc: str | None = None
        self.last_t: datetime | None = None  # start of the last fixation the filter took
        self.last_end: datetime | None = None  # end of the last fixation, for bridging blinks
        self.texts: OrderedDict[tuple[str, str], tuple[str, str, int]] = OrderedDict()  # tag, text, words
        self.layouts: OrderedDict[tuple[str, str], list[LineBox]] = OrderedDict()  # each block's lines
        self.page: PageReader | None = None  # line decoding on the page in front
        self.visit: Visit | None = None  # the block the line decoder has the eyes on, line by line
        self.pending: Visit | None = None  # another block the decoder moved to, until it holds
        self.reading: dict | None = None  # the line being read now, for /status
        # Where the last fixations went (NO_PAGE ... ON_LINE), the text the last page sent, and the
        # last point placed on a page: why no line is shown, and where the eyes are, for /status.
        self.outcomes: deque[int] = deque(maxlen=300)
        self.last_page: dict | None = None
        self.gaze_point: dict | None = None

    # -- the page in front -------------------------------------------------------------------------

    def browser(self, record: Record) -> list[dict]:
        """Follow the browser; returns dwell records that the change ended."""
        kind = getattr(record, "kind", None)
        if isinstance(record, View) and record.hidden:
            old = self.fronts.get(record.window)
            return self._leave(old) if old is not None and old.doc == record.doc else []
        if isinstance(record, Blocks | View):
            old = self.fronts.get(record.window)
            ended = self._leave(old) if old is not None and old.doc != record.doc else []
            if isinstance(record, Blocks):
                for block in record.blocks:
                    if block.text is not None:
                        self._remember(record.doc, block)
                    if block.lines is not None:
                        ended += self.layout(record.doc, block.id, [LineBox(*x.rect, x.text) for x in block.lines])
                blocks, measured = record.blocks, record.geometry.scroll_y
            elif old is not None and old.doc == record.doc:
                blocks, measured = old.blocks, old.blocks_scroll
            else:
                blocks, measured = [], record.geometry.scroll_y
            front = Front(
                record.tab, record.url, record.title, record.doc, record.geometry, blocks, measured, record.t,
                record.window,
            )  # fmt: skip
            for window, other in list(self.fronts.items()):  # a tab dragged to another window
                if other.tab == record.tab and window != record.window:
                    ended += self._leave(other)
            self.fronts[record.window] = front
            if self.front is None or self.front.window == record.window:
                self.front = front
            if isinstance(record, Blocks):
                lined = sum(bool(self.layouts.get((record.doc, b.id))) for b in record.blocks)
                self.last_page = {
                    "host": urlsplit(record.url).hostname,
                    "blocks": len(record.blocks),
                    "with_lines": lined,
                }
            return ended
        if isinstance(record, Click):
            self._click(record)
            return []
        if kind == "focus":
            # Another app taking focus leaves the browser's windows on screen: their pages stay. A
            # window that now shows another tab no longer shows its old page.
            self.focused = record.window
            old = self.fronts.get(record.window) if record.window is not None else None
            return self._leave(old) if old is not None and old.tab != record.tab else []
        if kind == "page" and "history_state" not in record.qualifiers:
            # A same-document URL change (pushState, scroll-spy pages) keeps the page; new content
            # shows up in the next snapshot.
            ended = []
            for other in [f for f in self.fronts.values() if f.tab == record.tab]:
                ended += self._leave(other)
            return ended
        if kind == "idle" and record.state == "locked":
            return self.clear()
        return []

    def _leave(self, front: Front) -> list[dict]:
        """A page is no longer on screen: its run and block visit end."""
        if self.fronts.get(front.window) is front:
            del self.fronts[front.window]
        ended = []
        if self.run is not None and self.run.doc == front.doc:
            ended += self.end()
        if self.visit is not None and self.visit.doc == front.doc:
            ended += self.end_visit()
        if self.belief_doc == front.doc:
            self.belief, self.belief_doc = {}, None
        if self.reading is not None and self.reading["doc"] == front.doc:
            self.reading = None
        if self.front is front:
            self.front = None
        if self.page is not None and self.page.doc == front.doc:
            self.page = None
        return ended

    def clear(self) -> list[dict]:
        """No page is on screen any more (locked, paused)."""
        self.fronts, self.front = {}, None
        self.belief, self.belief_doc = {}, None
        self.reading = None
        self.page = None
        return self.end() + self.end_visit()

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

    def layout(self, doc: str, block: str, lines: list[LineBox]) -> list[dict]:
        """A block's lines, new or re-measured; returns the record of a visit the change ended."""
        key = (doc, block)
        if self.layouts.get(key) == lines:
            return []
        self.layouts[key] = lines
        self.layouts.move_to_end(key)
        while len(self.layouts) > LAYOUT_CACHE:
            (old_doc, old_block), _ = self.layouts.popitem(last=False)
            if self.page is not None and self.page.doc == old_doc:
                self.page.remove_block(old_block)
        ended = []
        if self.visit is not None and (self.visit.doc, self.visit.block) == key:
            ended = self.end_visit()  # the wrapping changed: its line numbers mean other lines now
        if self.page is not None and self.page.doc == doc:
            self.page.set_block(block, lines)
        return ended

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
        """The fixation as a viewport point (CSS px), corrected by clicks, on the page whose
        window it falls in (the focused window's, when windows overlap), if any page on screen is
        on the tracker's monitor."""
        located, _ = self._place(fixation)
        return located

    def _place(self, fixation: Fixation) -> tuple[tuple[Front, float, float] | None, int]:
        """``_locate``, and why it found nothing (NO_PAGE, STALE, OTHER_MONITOR) or where the
        point fell (OUTSIDE every page's viewport, ON_PAGE)."""
        monitor = self.monitor
        if not self.fronts or monitor is None:
            return None, NO_PAGE
        fresh = [f for f in self.fronts.values() if abs((fixation.t - f.seen).total_seconds()) <= self.view_ttl_s]
        if not fresh:
            return None, STALE
        dx, dy = self.anchors.offset(fixation.x, fixation.y, fixation.t.timestamp())
        best, rank = None, None
        for front in fresh:
            g = front.geometry
            if not g.primary or abs(monitor[0] / monitor[1] - g.screen_w / g.screen_h) > 0.02:
                continue
            x = ((fixation.x - dx) * g.screen_w - g.vx) / g.scale
            y = ((fixation.y - dy) * g.screen_h - g.vy) / g.scale
            inside = 0 <= x <= g.inner_w and 0 <= y <= g.inner_h
            key = (inside, front.window == self.focused, front is self.front, front.seen)
            if rank is None or key > rank:
                best, rank = (front, x, y), key
        if best is None:
            return None, OTHER_MONITOR
        return best, ON_PAGE if rank[0] else OUTSIDE

    def live_point(self, doc: str, t: float, fx: float, fy: float) -> dict | None:
        """A live gaze point (monitor fractions, corrected by clicks) on the page ``doc`` shows,
        in its viewport's CSS px, with whether it falls inside the viewport; None when that page
        is not on screen on the tracker's monitor."""
        monitor = self.monitor
        front = next((f for f in self.fronts.values() if f.doc == doc), None)
        if front is None or monitor is None:
            return None
        g = front.geometry
        if not g.primary or abs(monitor[0] / monitor[1] - g.screen_w / g.screen_h) > 0.02:
            return None
        dx, dy = self.anchors.offset(fx, fy, t)
        x = ((fx - dx) * g.screen_w - g.vx) / g.scale
        y = ((fy - dy) * g.screen_h - g.vy) / g.scale
        inside = 0 <= x <= g.inner_w and 0 <= y <= g.inner_h
        return {"x": round(x, 1), "y": round(y, 1), "inside": inside}

    def _likelihoods(self, front: Front, x: float, y: float, now: float) -> dict[str, float]:
        g = front.geometry
        sigma = self.error_px(now) / RAYLEIGH_MEDIAN * (g.screen_w / self.monitor[0]) / g.scale  # monitor known
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
            stays = dict.fromkeys(like, math.exp(-self.rate * dt))
            run = self.run
            if self.restless and run is not None and run.doc == front.doc and run.block in stays:
                reading = self.reading_s(run)
                if reading is not None and run.dwell > reading:
                    # Past its reading time, the eyes are likelier to have moved on than at first.
                    stays[run.block] = math.exp(-self.rate * (1 + self.restless) * dt)
            # Belief on blocks that scrolled away or were re-rendered under new ids moves like
            # belief that leaves a block: to wherever eyes go when they move.
            vanished = max(0.0, 1.0 - sum(self.belief.get(k, 0.0) for k in like))
            moving = vanished + sum((1 - stays[k]) * self.belief.get(k, 0.0) for k in like)
            predicted = {k: stays[k] * self.belief.get(k, 0.0) + moving * jump[k] for k in like}
        else:
            predicted = jump
        self.belief = self._normalize({k: predicted[k] * like[k] ** self.evidence for k in like})
        self.last_t = t
        return self.belief

    def fixation(self, fixation: Fixation) -> tuple[Hit | None, list[dict]]:
        """Place one fixation and advance the run; returns the hit and any dwell records."""
        located, where = self._place(fixation)
        if located is not None:
            self.front = located[0]
        events = self.advance(fixation.t)  # gaze absent since the last fixation ended
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
                self.away, self.challenger = None, None
            else:
                self.away = self.away or fixation.t
                if key is not None:  # another block leads: where the eyes went, if they stay away
                    challenger = self.challenger
                    if challenger is None or challenger.key != key:
                        self.challenger = self._run(key, fixation, duration)
                    else:
                        challenger.dwell += duration
                        challenger.t1, challenger.fixations = fixation.t_end, challenger.fixations + 1
                if (fixation.t_end - self.away).total_seconds() > self.excursion_s:
                    challenger = self.challenger
                    events += self.end()
                    if challenger is not None and challenger.key == key:
                        self.run = challenger  # it led all along the excursion: that time is its own
        if self.run is None and key is not None and self.front is not None:
            self.run = self._run(key, fixation, duration)
        line = None
        if located is not None:
            line, ended = self._read(located, fixation, duration)
            events += ended
            if line is not None and hit is not None:
                hit = Hit(hit.tab, hit.doc, hit.x, hit.y, hit.block, hit.p, line[0], line[1], round(line[2], 2))
        self.outcomes.append(ON_LINE if line is not None else where)
        if hit is not None:
            self.gaze_point = {
                "t": fixation.t_end.isoformat(), "doc": hit.doc, "x": hit.x, "y": hit.y,
                "scroll_y": located[0].geometry.scroll_y, "block": hit.block,
            }  # fmt: skip
        if self.monitor is not None:
            self.anchors.fixation(fixation.t.timestamp(), fixation.t_end.timestamp(), fixation.x, fixation.y)
        run = self.run
        if run is not None and not run.fired and run.dwell >= self.limit(run) and self._resolvable(run):
            run.fired = True
            events.append(self._record(run, "start"))
        return hit, events

    def _read(
        self, located: tuple[Front, float, float], fixation: Fixation, duration: float
    ) -> tuple[tuple[str, int, float] | None, list[dict]]:
        """Feed the line decoder; returns the line it has the eyes on (block, line, probability),
        if any, and the record of a block visit that ended."""
        front, x, y = located
        g = front.geometry
        if self.monitor is None or not (0 <= x <= g.inner_w and 0 <= y <= g.inner_h):
            return None, []  # off the page: the toolbar, another window
        reader = self._reader(front, fixation.t.timestamp())
        if not reader.blocks:
            return None, []
        t, t_end = fixation.t.timestamp(), fixation.t_end.timestamp()
        reader.update(x, y + g.scroll_y, t, (g.scroll_y, g.scroll_y + g.inner_h, g.inner_w))
        key, p = reader.best()
        if key is OFF:
            return None, []
        block, line = key
        ended: list[dict] = []
        visit = self.visit
        if visit is not None and t - visit.t1 > VISIT_GAP_S:
            ended, visit = self.end_visit(), None
        if visit is not None and visit.block == block:
            visit.add(line, t, t_end, duration, p)
            visit.offset_px, self.pending = reader.offset(), None
        else:
            # Another block: a new visit once two fixations in a row are decoded there.
            pending = self.pending
            if pending is None or pending.block != block:
                texts = [b.text for b in reader.blocks[block]]
                pending = self.pending = Visit(
                    reader.doc, block, len(texts), t, texts=texts, tab=front.tab, url=front.url, title=front.title
                )
            pending.add(line, t, t_end, duration, p)
            pending.offset_px = reader.offset()
            if visit is None or pending.fixations >= 2:
                ended += self.end_visit()
                self.visit, self.pending = pending, None
        tag, _, _ = self.texts.get((reader.doc, block), (None, None, None))
        self.reading = {
            "t": fixation.t_end.isoformat(), "url": front.url, "doc": reader.doc, "block": block, "tag": tag,
            "line": line, "lines": len(reader.blocks[block]), "p": round(p, 2), "text": reader.text(key),
        }  # fmt: skip
        return (block, line, p), ended

    def _reader(self, front: Front, now: float) -> PageReader:
        """The line decoder for the page in front, made anew for another page or another scale."""
        g = front.geometry
        sigma = self.error_px(now) / RAYLEIGH_MEDIAN * (g.screen_w / self.monitor[0]) / g.scale  # CSS px
        reader = self.page
        if reader is None or reader.doc != front.doc or abs(reader.spread / sigma - 1) > 0.25:
            reader = self.page = PageReader(front.doc, max(SCATTER * sigma, 4.0), max(OFFSET * sigma, 4.0))
            reader.spread = sigma
            for (doc, block), lines in self.layouts.items():
                if doc == front.doc:
                    reader.set_block(block, lines)
        return reader

    def _run(self, key: tuple[str, str], fixation: Fixation, duration: float) -> Run:
        front = self.front
        return Run(
            uuid.uuid4().hex[:16], front.tab, front.url, front.title, front.doc, key[1],
            fixation.t, fixation.t_end, duration, 1,
        )  # fmt: skip

    def _resolvable(self, run: Run) -> bool:
        """Whether the tracker, at its current error, can single out the run's block: a look at
        its middle would make it the likeliest block by at least ``resolve``. When the error is
        larger than the space between blocks, time near a small block (a heading, a one-line
        paragraph) is mostly time on its neighbours, and a dwell on it would be a guess."""
        front = self.front
        if self.resolve <= 0:
            return True
        if front is None or front.doc != run.doc or self.monitor is None:
            return False  # nothing to judge it against now; the next fixation tries again
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
        """End the run if no gaze at all has arrived for longer than ``max_gap_s`` (eyes closed, off
        the screen, face lost). Gaze elsewhere on the screen is ``excursion_s``'s business."""
        last = self.last_end if self.last_end is not None else (self.run.t1 if self.run else None)
        out = []
        if self.run is not None and last is not None and (now - last).total_seconds() > self.max_gap_s:
            out += self.end()
        if self.visit is not None and now.timestamp() - self.visit.t1 > VISIT_GAP_S:
            out += self.end_visit()
        return out

    def end(self) -> list[dict]:
        run, self.run, self.away, self.challenger = self.run, None, None, None
        return [self._record(run, "end")] if run is not None and run.fired else []

    def end_visit(self) -> list[dict]:
        """End the line decoder's visit to a block: what was read on it, line by line, if the eyes
        were there long enough to call it reading (3 fixations, 1.5 s)."""
        visit, self.visit, self.pending = self.visit, None, None
        if visit is None or visit.fixations < 3 or visit.t1 - visit.t0 < 1.5:
            return []
        tag, _, _ = self.texts.get((visit.doc, visit.block), (None, None, None))
        zone = self.last_end.tzinfo if self.last_end is not None else None
        return [{
            "id": uuid.uuid4().hex[:16] + "-read", "kind": "read",
            "t": datetime.fromtimestamp(visit.t0, zone).isoformat(),
            "t_end": datetime.fromtimestamp(visit.t1, zone).isoformat(),
            "tab": visit.tab, "url": visit.url, "title": visit.title, "doc": visit.doc, "block": visit.block,
            "tag": tag, **visit.summary(),
        }]  # fmt: skip

    def lines_health(self) -> dict:
        """Why a line is or is not being decoded: where the last fixations went, and the last
        page's text."""
        count = [0] * 6
        for outcome in self.outcomes:
            count[outcome] += 1
        return {
            "fixations": len(self.outcomes),
            "on_page": count[ON_PAGE] + count[ON_LINE],
            "on_line": count[ON_LINE],
            "outside": count[OUTSIDE],  # on the monitor, but outside every page shown
            "no_page": count[NO_PAGE],  # no browser page on screen (or no monitor from the tracker)
            "stale": count[STALE],  # pages not heard from for a while (minimized, hidden)
            "other_monitor": count[OTHER_MONITOR],  # the page is on another monitor
            "page": self.last_page,
        }

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

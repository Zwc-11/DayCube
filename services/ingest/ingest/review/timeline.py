"""The day as one timeline: at the desk doing what, away, at a place, talking.

``vault/<date>/timeline.jsonl`` holds the segments of a review day, from 04:00 local on the date to
04:00 the next morning (``DAY_START_H``): a night that runs past midnight belongs to the day it
began. Each line is one segment (``packages/schemas/timeline.schema.json``)::

    {"t", "until", "where": "desk" | "away" | "place:<id>" | "unknown", "doing", "category",
     "project", "talk": [moment ids], "switches", "evidence": [record ids],
     "source": "pc" | "phone" | "pc+phone", "fidelity", "labeled"}

Where it comes from:

- **At the desk**: the app in front, from the screen reader (``windows.jsonl``, a record at each
  change), and while a browser is in front, the page in front, from the extension
  (``browser.jsonl``). Presence is the screen reader's: away starts at the last input once five
  minutes pass with no input and no gaze, at the lock screen, or when the computer slept. The
  extension's own one-minute idle is not used where ``windows.jsonl`` exists, so reading a page
  without touching anything still counts. A video that plays on in the page in front is watching,
  not away. A day without ``windows.jsonl`` falls back to the extension's visits and its idle.
- **Away**: those ``away`` records (``idle``, ``locked``, ``asleep``). Paused recording, a stopped
  receiver, reading switched off and the bare desktop are not recorded time: no segment.
- **Places**: the phone's visits (``day.json``) and transits (``events.jsonl``) take over the away
  time they overlap and fill time the computer did not record. Time at the desk stays at the desk.
- **Talk**: a recorded conversation (a moment with a transcript) is listed on the segment it
  overlaps most, or is a segment of its own (``where: unknown``) when nothing else covers it.

Categories and projects are ``labels.py``'s. Pieces with the same place, category and project
merge across gaps up to ``MERGE_GAP_S``. Something else in front for under ``SWITCH_S`` beside desk
time is a switch: counted on the segment it interrupted, not a segment of its own.

Capture folders are calendar days, so the records of the day before and the day after are read
too: a window in front across midnight, or since the evening before, is one span.
"""

from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import contracts
from ..capture.synthesize import LOCK_APPS, MAX_APP_SPAN_S, Visit, _media, _records, _tab_spans, app_name
from ..contracts import ContractError
from ..time import instant
from ..vault import Record, Vault, file_stamp, read_lines, read_text, stored
from .labels import CACHE_FILE, CORRECTIONS_FILE, OTHER, PROJECTS_FILE, RULES_FILE, Activity, Label, Labeler, Labels

FILE = "timeline.jsonl"
DAY_START_H = 4  # the review day runs from 04:00 to 04:00 local
MERGE_GAP_S = 60.0  # the same kind of thing again within this long continues the segment
SWITCH_S = 20.0  # in front for less than this beside desk time: a switch, not a segment
QUIET_S = 360.0  # no record this long from a reader that writes "still" every 300 s: it stopped
AWAY = {"idle": "Away", "locked": "Locked", "asleep": "Asleep"}  # away reasons that mean away
TALK_TYPES = ("moment.audio", "moment.video")
PRIVATE = "private"  # a browser in front with a page the extension does not record


def window(day: str, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The review day ``day``: from 04:00 local to 04:00 the next day (23 or 25 hours across a
    clock change)."""
    first = date.fromisoformat(day)
    after = first + timedelta(days=1)
    return (
        datetime(first.year, first.month, first.day, DAY_START_H, tzinfo=zone),
        datetime(after.year, after.month, after.day, DAY_START_H, tzinfo=zone),
    )


def review_day(moment: datetime, zone: ZoneInfo) -> str:
    """The review day a moment belongs to: before 04:00 it is still the day before."""
    local = moment.astimezone(zone)
    return (local.replace(tzinfo=None) - timedelta(hours=DAY_START_H)).date().isoformat()


def _neighbours(day: str) -> list[str]:
    first = date.fromisoformat(day)
    return [(first + timedelta(days=n)).isoformat() for n in (-1, 0, 1)]


@dataclass(slots=True)
class Piece:
    """A stretch of one thing: the unit segments are made of. Times are seconds since the epoch."""

    t0: float
    t1: float
    where: str  # desk, away, unknown, place:<id>
    doing: str
    evidence: tuple[str, ...]
    source: str  # pc, phone, pc+phone
    fidelity: str  # known, estimated, illustrative (from synthetic or sample records)
    activity: Activity | None = None  # at the desk: what was in front
    kind: str = ""  # away: "idle", "locked", "asleep" or "transit"; a place: "place"
    label: Label | None = None


@dataclass(slots=True)
class Talk:
    t0: float
    t1: float
    moment: str
    title: str
    evidence: str
    fidelity: str = "known"


@dataclass(slots=True)
class _State:
    """What ``windows.jsonl`` says was in front from ``t0`` to ``t1``: an app, a browser, or away."""

    t0: float
    t1: float
    kind: str  # app, browser, away
    record: Record


@dataclass
class Built:
    day: str
    zone: str
    segments: list[Record]
    report: Record = field(default_factory=dict)


# -- reading ------------------------------------------------------------------------------------


def _timed(records: list[Record]) -> list[tuple[float, Record]]:
    """(epoch seconds, record) in time order; a record whose time does not parse is skipped."""
    keyed = []
    for index, record in enumerate(records):
        try:
            keyed.append((instant(record["t"]).timestamp(), index, record))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    keyed.sort(key=lambda item: (item[0], item[1]))
    return [(t, r) for t, _, r in keyed]


def _seconds(value: str) -> float:
    return instant(value).timestamp()


def _capture(vault: Vault, days: list[str]) -> tuple[list[tuple[float, Record]], list[tuple[float, Record]], list[str]]:
    """The browser and window records of ``days``, and the days the screen reader recorded."""
    browser: list[Record] = []
    windows: list[Record] = []
    read = []
    for day in days:
        folder = vault.root / "capture" / day
        browser += _records(folder / "browser.jsonl")
        if stored(folder / "windows.jsonl").exists():
            windows += _records(folder / "windows.jsonl")
            read.append(day)
    return _timed(browser), _timed(windows), read


def _states(windows: list[tuple[float, Record]], end: float) -> list[_State]:
    """What was in front, from one ``windows.jsonl`` record to the next. The reader writes a
    ``still`` record every ``STILL_S`` while it records; when the records stop for longer than that
    (the computer switched off, the receiver killed), what was in front ends at the last one. From
    readers without ``still`` records, an app or browser in front longer than ``MAX_APP_SPAN_S``
    with no other record ends there."""
    states: list[_State] = []
    current: _State | None = None
    heard: float | None = None  # the last record of any kind
    still: float | None = None  # the last ``still`` record

    def close(t: float) -> None:
        nonlocal current
        if current is not None:
            # A reader that says it is still recording has proved it; one from before ``still``
            # records could have stopped at any point, so a long span with no record is cut.
            capped = current.kind != "away" and still is None
            t1 = min(t, current.t0 + MAX_APP_SPAN_S) if capped else t
            if t1 > current.t0:
                current.t1 = t1
                states.append(current)
        current = None

    def quiet(t: float) -> bool:
        """The reader writes ``still`` records and fell silent before ``t``."""
        return heard is not None and still is not None and t - heard > QUIET_S and heard - still <= QUIET_S

    for t, record in windows:
        kind = record.get("kind")
        app = str(record.get("app") or "").casefold()
        if quiet(t) and not record.get("since"):  # an away with ``since`` says when it began
            close(heard)  # type: ignore[arg-type]  # nothing is known after the last record
        heard = t
        if kind == "still":
            still = t
            continue
        if kind == "away" and record.get("why") in AWAY:
            # Input that comes back on its own every five minutes (2026-10-24: 222 of the
            # extension's 276 returns from idle came 300 s after the last input; the source is
            # unknown) logs away, back a second later, away again since that second: the away
            # stays whole, with no time between.
            if current is not None and current.kind == "away":
                continue  # idle, then locked: still away
            start = t
            if record.get("since"):
                with suppress(TypeError, ValueError):
                    start = min(t, _seconds(record["since"]))  # away since the last input
                floor = current.t0 if current is not None else (states[-1].t1 if states else start)
                start = max(start, floor)
            close(start)
            current = _State(start, start, "away", record)
        elif kind == "away":
            close(t)  # paused, stopped, reading off, the bare desktop: not recorded from here
        elif kind == "window" and app in LOCK_APPS:
            if current is None or current.kind != "away":
                close(t)
                current = _State(t, t, "away", {**record, "why": "locked"})
        elif kind == "window" and app:
            close(t)
            current = _State(t, t, "browser" if record.get("browser") else "app", record)
    close(heard if quiet(end) else end)  # type: ignore[arg-type]
    return states


def _spans(visits: list[Visit], *, cap: bool) -> tuple[list[float], list[float], list[Visit]]:
    """Visits as sorted start and end times. ``cap``: a page in front longer than
    ``MAX_APP_SPAN_S`` with no other record ends there, as an app from an old reader does (the
    extension's own visits, where the screen reader did not run). Pages inside a browser's time in
    front need no cap: that time bounds them."""
    starts, ends, items = [], [], []
    for visit in visits:
        a = _seconds(visit.t0)
        b = min(_seconds(visit.t1), a + MAX_APP_SPAN_S) if cap else _seconds(visit.t1)
        if b > a:
            starts.append(a)
            ends.append(b)
            items.append(visit)
    return starts, ends, items


def _fidelity(record: Record, default: str = "known") -> str:
    """Records written by a simulation or a sample (``"synthetic": true``) stay identifiable."""
    return "illustrative" if record.get("synthetic") else default


def _page_piece(visit: Visit, a: float, b: float, program: str, synthetic: frozenset[str] = frozenset()) -> Piece:
    activity = Activity.page(visit.url, visit.title, program)
    fidelity = "illustrative" if visit.first_id in synthetic else "known"
    return Piece(a, b, "desk", activity.doing, (f"capture:visit:{visit.first_id}",), "pc", fidelity, activity)


def _no_page(record: Record, a: float, b: float) -> Piece:
    """A browser in front with no page recorded: a page the extension is told not to record, a
    private window, or the extension off. Its own category, so that what cannot be known is not
    counted as what the rules failed to know."""
    name = app_name(str(record.get("app") or "browser"))
    label = Label(PRIVATE, None, "rule")
    return Piece(a, b, "desk", f"{name}: a page not recorded", (f"capture:app:{record.get('id')}",), "pc",
                 _fidelity(record), None, label=label)  # fmt: skip


def _browser(
    state: _State, pages: tuple[list[float], list[float], list], synthetic: frozenset[str] = frozenset()
) -> list[Piece]:
    """The pages in front while a browser was. Gaps under ``MERGE_GAP_S`` (the extension reports a
    moment after the window comes to front, a flip shorter than it reports) go to the next page,
    or the last one."""
    starts, ends, items = pages
    program = str(state.record.get("app") or "")
    out: list[Piece] = []
    cursor = state.t0
    for index in range(bisect_right(ends, state.t0), bisect_left(starts, state.t1)):
        a, b = max(starts[index], state.t0), min(ends[index], state.t1)
        if b <= a:
            continue
        if a - cursor >= MERGE_GAP_S:
            out.append(_no_page(state.record, cursor, a))
        elif a > cursor:
            a = cursor
        out.append(_page_piece(items[index], a, b, program, synthetic))
        cursor = b
    if state.t1 - cursor >= MERGE_GAP_S or (not out and state.t1 > cursor):
        out.append(_no_page(state.record, cursor, state.t1))
    elif state.t1 > cursor:
        out[-1].t1 = state.t1
    return out


def _uncovered(
    covered: list[tuple[float, float]], pages: tuple[list[float], list[float], list[Visit]],
    synthetic: frozenset[str] = frozenset(),
) -> list[Piece]:  # fmt: skip
    """Pages from the extension, with its own idle, outside ``covered`` (sorted, disjoint): the
    days the screen reader did not run. Where it ran, the time it says nothing about stays
    unrecorded: the extension cannot tell a page left in front from one being read (2026-10-29,
    a tab "in front" for 12 hours while the computer slept)."""
    starts, ends, items = pages
    edges = covered
    lows = [a for a, _ in edges]
    out = []
    for a, b, visit in zip(starts, ends, items, strict=True):
        cursor = a
        for low, high in edges[max(0, bisect_right(lows, a) - 1) : bisect_left(lows, b)]:
            if high <= cursor:
                continue
            if low > cursor:
                out.append(_page_piece(visit, cursor, min(low, b), "", synthetic))
            cursor = max(cursor, high)
            if cursor >= b:
                break
        if cursor < b:
            out.append(_page_piece(visit, cursor, b, "", synthetic))
    return out


def _watching(pieces: list[Piece], media: list[Record]) -> list[Piece]:
    """Away for want of input while a video went on playing in the page in front: watching, until
    the video stopped."""
    playing = []
    for record in media:
        try:
            url = str(record.get("url") or "").split("#")[0]
            playing.append((_seconds(record["t"]), _seconds(record["t_end"]), url, str(record.get("id"))))
        except (KeyError, TypeError, ValueError):
            continue
    if not playing:
        return pieces
    out: list[Piece] = []
    for piece in pieces:
        before = out[-1] if out else None
        page = before.activity.url if before is not None and before.activity is not None else ""
        if piece.kind != "idle" or before is None or not page or abs(before.t1 - piece.t0) > 1.0:
            out.append(piece)
            continue
        # A video of that page, playing when the input stopped, kept the person there until it stopped.
        found = [(min(m1, piece.t1), media_id) for m0, m1, url, media_id in playing
                 if url == page and m0 <= piece.t0 + 1.0 and m1 > piece.t0]  # fmt: skip
        if not found:
            out.append(piece)
            continue
        until, media_id = max(found)
        if piece.t1 - until < 1.0:  # paused as the person came back: the reader's next round
            until = piece.t1
        out.append(replace(before, t0=piece.t0, t1=until, evidence=(f"capture:media:{media_id}",)))
        if until < piece.t1:
            out.append(replace(piece, t0=until))
    return out


def _covered(states: list[_State], read: list[str], zone: ZoneInfo) -> list[tuple[float, float]]:
    """The states, and the whole of each calendar day the screen reader recorded, merged."""
    edges = sorted([(s.t0, s.t1) for s in states] + [
        tuple(datetime.combine(date.fromisoformat(d) + timedelta(days=n), datetime.min.time(), zone).timestamp()
              for n in (0, 1))
        for d in read
    ])  # fmt: skip
    merged: list[tuple[float, float]] = []
    for a, b in edges:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _pc(
    browser: list[tuple[float, Record]], windows: list[tuple[float, Record]], read: list[str], end: float,
    zone: ZoneInfo,
) -> list[Piece]:  # fmt: skip
    """Desk and away pieces from the computer, in time order, never overlapping. ``read``: the
    calendar days with ``windows.jsonl``."""
    end_iso = datetime.fromtimestamp(end, UTC).isoformat()
    records = [r for _, r in browser]
    synthetic = frozenset(str(r.get("id")) for r in records if r.get("synthetic"))
    front = _spans(_tab_spans([r for r in records if r.get("kind") != "idle"], end_iso), cap=False)
    states = _states(windows, end)
    pieces: list[Piece] = []
    for state in states:
        record = state.record
        if state.kind == "away":
            why = str(record.get("why"))
            fidelity = _fidelity(record, "estimated" if why == "idle" else "known")
            pieces.append(Piece(state.t0, state.t1, "away", AWAY[why], (f"capture:away:{record.get('id')}",), "pc",
                                fidelity, kind=why))  # fmt: skip
        elif state.kind == "browser":
            pieces += _browser(state, front, synthetic)
        else:
            title = "" if record.get("private") else str(record.get("title") or "")
            activity = Activity.app(str(record["app"]), title)
            pieces.append(Piece(state.t0, state.t1, "desk", activity.doing, (f"capture:app:{record.get('id')}",),
                                "pc", _fidelity(record), activity))  # fmt: skip
    pieces += _uncovered(_covered(states, read, zone), _spans(_tab_spans(records, end_iso), cap=True), synthetic)
    pieces.sort(key=lambda p: p.t0)
    return _watching(pieces, _media(records))


# -- the phone ----------------------------------------------------------------------------------


def _phone(vault: Vault, days: list[str], errors: list[str]) -> tuple[list[Piece], list[Talk]]:
    """Places and transits from the phone, and the conversations it recorded."""
    pieces: list[Piece] = []
    talks: list[Talk] = []
    names = vault.place_names()
    for day in days:
        if not (vault.day_path(day, must_exist=False) / "day.json").is_file():
            continue
        try:
            snapshot = vault.snapshot(day)
            events = snapshot.events.records
            # A day whose records are a sample (the fixture day) or a simulation stays marked.
            sample = any(e["fidelity"] == "illustrative" or e["source"]["kind"] == "synthetic" for e in events)
            for place in snapshot.day["places"]:
                name = names.get(place["id"], place["name"])
                pieces.append(Piece(_seconds(place["t0"]), _seconds(place["t1"]), f"place:{place['id']}", name,
                                    (f"place:{place['id']}",), "phone", "illustrative" if sample else "estimated",
                                    kind="place"))  # fmt: skip
            recorded = {e["moment_id"]: e["id"] for e in events if e["type"] in TALK_TYPES and e.get("moment_id")}
            for event in events:
                if event["type"] == "place.transit" and event.get("t_end"):
                    fidelity = "illustrative" if event["fidelity"] == "illustrative" or sample else "estimated"
                    pieces.append(Piece(_seconds(event["t"]), _seconds(event["t_end"]), "away", event["label"],
                                        (event["id"],), "phone", fidelity, kind="transit"))  # fmt: skip
            for moment_id, moment in snapshot.moments.items():
                if not any(segment["text"].strip() for segment in moment.transcript["segments"]):
                    continue
                t0 = _seconds(moment.meta["t0"])
                evidence = recorded.get(moment_id) or f"transcript:{moment_id}:0"
                talks.append(Talk(t0, t0 + float(moment.meta["duration_s"]), moment_id,
                                  moment.meta.get("title") or "Conversation", evidence,
                                  "illustrative" if sample or moment.meta["fidelity"] == "illustrative" else "known"))  # fmt: skip
        except (ContractError, OSError, ValueError, KeyError) as error:
            errors.append(f"{day}: the phone's records were left out ({error})")
    pieces.sort(key=lambda p: (p.t0, p.t1))
    for before, after in zip(pieces, pieces[1:], strict=False):  # overlapping visits: the later one wins
        before.t1 = min(before.t1, after.t0)
    return [p for p in pieces if p.t1 > p.t0], sorted(talks, key=lambda t: t.t0)


def _overlay(pc: list[Piece], phone: list[Piece], talks: list[Talk]) -> list[Piece]:
    """One sequence: desk time stays at the desk; away time is the phone's place or transit when it
    has one; time only the phone recorded is the phone's; a conversation nothing else covers is a
    piece of its own."""
    cuts = sorted({p.t0 for p in pc} | {p.t1 for p in pc} | {p.t0 for p in phone} | {p.t1 for p in phone}
                  | {t.t0 for t in talks} | {t.t1 for t in talks})  # fmt: skip
    out: list[Piece] = []
    origin: tuple = ()
    i = j = 0
    for a, b in zip(cuts, cuts[1:], strict=False):
        mid = (a + b) / 2
        while i < len(pc) and pc[i].t1 <= mid:
            i += 1
        while j < len(phone) and phone[j].t1 <= mid:
            j += 1
        p = pc[i] if i < len(pc) and pc[i].t0 <= mid else None
        q = phone[j] if j < len(phone) and phone[j].t0 <= mid else None
        if p is not None and (p.where == "desk" or q is None):
            piece, now = p, (id(p),)
        elif q is not None:
            piece = q if p is None else replace(q, source="pc+phone", evidence=p.evidence + q.evidence)
            now = (id(p), id(q))
        else:
            talk = next((t for t in talks if t.t0 <= mid < t.t1), None)
            if talk is None:
                continue
            piece = Piece(talk.t0, talk.t1, "unknown", talk.title, (talk.evidence,), "phone", talk.fidelity, kind="talk")
            now = (id(talk),)
        if out and origin == now and out[-1].t1 == a:
            out[-1].t1 = b
        else:
            out.append(replace(piece, t0=a, t1=b))
        origin = now
    return out


# -- segments -----------------------------------------------------------------------------------


def _key(piece: Piece) -> tuple:
    if piece.where == "desk":
        label = piece.label or Label(OTHER, None, "none")
        return ("desk", label.category, label.project)
    if piece.where == "away":
        return ("away", "transit" if piece.kind == "transit" else "pc")
    return (piece.where,)


@dataclass
class _Run:
    t0: float
    t1: float
    key: tuple
    pieces: list[Piece]
    switches: int = 0

    @property
    def desk(self) -> bool:
        return self.key[0] == "desk"

    def take(self, other: _Run, *, switch: bool) -> None:
        self.t0, self.t1 = min(self.t0, other.t0), max(self.t1, other.t1)
        self.pieces += other.pieces
        self.switches += other.switches + (1 if switch else 0)


def _runs(pieces: list[Piece]) -> list[_Run]:
    """Pieces merged into segments, and short desk runs counted as switches."""
    runs: list[_Run] = []
    for piece in pieces:
        key = _key(piece)
        last = runs[-1] if runs else None
        if last is not None and last.key == key and piece.t0 - last.t1 <= MERGE_GAP_S:
            last.t1 = max(last.t1, piece.t1)
            last.pieces.append(piece)
        else:
            runs.append(_Run(piece.t0, piece.t1, key, [piece]))
    out: list[_Run] = []
    for index, run in enumerate(runs):
        if run.desk and run.t1 - run.t0 < SWITCH_S:
            before = out[-1] if out else None
            after = runs[index + 1] if index + 1 < len(runs) else None
            if before is not None and before.desk and run.t0 - before.t1 <= MERGE_GAP_S:
                before.take(run, switch=True)
                continue
            if (
                after is not None
                and after.desk
                and after.t0 - run.t1 <= MERGE_GAP_S
                and after.t1 - after.t0 >= SWITCH_S
            ):
                after.take(run, switch=True)
                continue
        before = out[-1] if out else None
        if before is not None and before.key == run.key and run.t0 - before.t1 <= MERGE_GAP_S:
            before.take(run, switch=False)
        else:
            out.append(run)
    return out


def _iso(t: float, zone: ZoneInfo) -> str:
    """Local time with its offset, to the microsecond the records carry (whole seconds plainly)."""
    moment = datetime.fromtimestamp(t, zone)
    return moment.isoformat(timespec="microseconds" if moment.microsecond else "seconds")


def _segment(run: _Run, zone: ZoneInfo) -> Record:
    spent: dict[str, float] = defaultdict(float)
    by: dict[str, float] = defaultdict(float)
    activities: dict[Activity, float] = defaultdict(float)
    for piece in run.pieces:
        spent[piece.doing] += piece.t1 - piece.t0
        if piece.label is not None and _key(piece) == run.key:
            by[piece.label.by] += piece.t1 - piece.t0
            if piece.activity is not None:
                activities[piece.activity] += piece.t1 - piece.t0
    sources = {p.source for p in run.pieces}
    source = "pc+phone" if "pc+phone" in sources or {"pc", "phone"} <= sources else sources.pop()
    fidelities = {p.fidelity for p in run.pieces}
    main = max(activities, key=lambda a: activities[a]) if activities else None  # what a relabel changes
    return {
        "t": _iso(run.t0, zone),
        "until": _iso(run.t1, zone),
        "where": run.key[0],
        "doing": max(spent, key=lambda d: spent[d]),
        "category": run.key[1] if run.desk else None,
        "project": run.key[2] if run.desk else None,
        "talk": [],
        "switches": run.switches,
        "evidence": list(dict.fromkeys(e for p in run.pieces for e in p.evidence)),
        "source": source,
        "fidelity": next((f for f in ("illustrative", "estimated") if f in fidelities), "known"),
        "labeled": (max(by, key=lambda k: by[k]) if by else "none") if run.desk else None,
        "key": main.key if main else None,
        "broad": main.broad if main else None,
    }


# -- building -----------------------------------------------------------------------------------


def build(vault: Vault, day: str, zone: str, *, now: datetime | None = None, labeler: Labeler | None = None) -> Built:
    """The timeline of review day ``day``. ``now``: while the receiver runs, what is in front now
    is still in front (otherwise the records' last moment ends it). ``labeler``: the language model
    for apps and sites no rule knows (``labels.llm_labeler``)."""
    days = _neighbours(day)
    own = vault.day_path(day, must_exist=False) / "day.json"
    if own.is_file():
        with suppress(ContractError, OSError, ValueError, KeyError):  # an unreadable day is reported below
            zone = vault.snapshot(day).day["tz"]
    tz = ZoneInfo(zone)
    start, stop = (t.timestamp() for t in window(day, tz))
    errors: list[str] = []
    browser, windows, read = _capture(vault, days)
    seen = [t for t, _ in browser[-1:] + windows[-1:]]
    end = now.timestamp() if now is not None else max(seen, default=start)
    pc = _pc(browser, windows, read, end, tz) if seen else []
    phone, talks = _phone(vault, days, errors)
    pieces = []
    for piece in _overlay(pc, phone, talks):
        a, b = max(piece.t0, start), min(piece.t1, stop)
        if b > a:
            pieces.append(replace(piece, t0=a, t1=b) if (a, b) != (piece.t0, piece.t1) else piece)
    labels = Labels.load(vault)
    seconds: dict[Activity, float] = defaultdict(float)
    for piece in pieces:
        if piece.activity is not None and piece.label is None:
            seconds[piece.activity] += piece.t1 - piece.t0
    labelled = labels.label_all(dict(seconds), vault, labeler, now)
    errors += labels.errors  # files left out, a failed call to the model
    for piece in pieces:
        if piece.activity is not None and piece.label is None:
            piece.label = labelled[piece.activity]
    runs = _runs(pieces)
    segments = [_segment(run, tz) for run in runs]
    for talk in talks:
        overlap = [(min(talk.t1, r.t1) - max(talk.t0, r.t0), n) for n, r in enumerate(runs)]
        best = max(overlap, default=(0.0, -1))
        if best[0] > 0:
            segments[best[1]]["talk"].append(talk.moment)
    contracts.validate_all("timeline", segments)
    return Built(day, zone, segments, _report(runs, labels, errors))


def _report(runs: list[_Run], labels: Labels, errors: list[str]) -> Record:
    desk = [r for r in runs if r.desk]
    by_category: dict[str, float] = defaultdict(float)
    by_label: dict[str, float] = defaultdict(float)
    other: dict[str, float] = defaultdict(float)
    for run in desk:
        by_category[run.key[1]] += run.t1 - run.t0
        for piece in run.pieces:
            if piece.label is not None:
                by_label[piece.label.by] += piece.t1 - piece.t0
            if piece.label is not None and piece.label.category == OTHER:  # what no rule knows, by name
                other[piece.activity.name if piece.activity else piece.doing] += piece.t1 - piece.t0
    desk_s = sum(by_category.values())
    return {
        "segments": len(runs),
        "desk_s": round(desk_s, 1),
        "away_s": round(sum(r.t1 - r.t0 for r in runs if r.key[0] == "away"), 1),
        "place_s": round(sum(r.t1 - r.t0 for r in runs if r.key[0].startswith("place:")), 1),
        "switches": sum(r.switches for r in runs),
        "other_share": round(by_category.get(OTHER, 0.0) / desk_s, 4) if desk_s else None,
        "by_category": {k: round(v, 1) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])},
        "labeled_by": {k: round(v, 1) for k, v in sorted(by_label.items(), key=lambda kv: -kv[1])},
        "other_top": [[k, round(v, 1)] for k, v in sorted(other.items(), key=lambda kv: -kv[1])[:10]],
        "llm": labels.llm,
        "errors": list(dict.fromkeys(errors)),
    }


def write(vault: Vault, day: str, zone: str, *, now: datetime | None = None, labeler: Labeler | None = None) -> Record:
    """Build the timeline of ``day`` and write it to ``vault/<day>/timeline.jsonl``; returns its
    report with ``segments`` as a count. A day with nothing recorded gets no file, and a file
    whose content would not change is left as it is (its stamp stays, so what reads it does not
    redo its work)."""
    return write_built(vault, build(vault, day, zone, now=now, labeler=labeler))


def write_built(vault: Vault, built: Built) -> Record:
    path = vault.day_path(built.day, must_exist=False) / FILE
    written = bool(built.segments) or stored(path).exists()
    if written:
        text = "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in built.segments)
        try:
            same = read_text(path) == text
        except OSError:
            same = False
        if not same:
            vault.write_lines(path, built.segments)
    return {"date": built.day, "file": path.relative_to(vault.root).as_posix(), "written": written, **built.report}


def read(vault: Vault, day: str) -> list[Record] | None:
    """The segments written for ``day``, or None when there is no timeline file."""
    path = vault.day_path(day, must_exist=False) / FILE
    if not stored(path).exists():
        return None
    return read_lines(path)


def has_inputs(vault: Vault, day: str) -> bool:
    """Whether anything could fall in the review day: capture or a phone day on its dates."""
    for name in _neighbours(day)[1:]:
        folder = vault.root / "capture" / name
        if any(stored(folder / log).exists() for log in ("browser.jsonl", "windows.jsonl")):
            return True
        if (vault.root / name / "day.json").is_file():
            return True
    return False


def inputs_stamp(vault: Vault, day: str) -> dict[str, list[int] | None]:
    """The stamp of every file a review day's timeline is made from: rebuild when it changes."""
    files = []
    for name in _neighbours(day):
        files += [vault.root / "capture" / name / log for log in ("browser.jsonl", "windows.jsonl")]
        folder = vault.root / name
        files += [folder / "day.json", folder / "events.jsonl"]
        if (folder / "moments").is_dir():
            for moment in sorted((folder / "moments").iterdir()):
                files += [moment / "meta.json", moment / "transcript.json"]
    files += [vault.root / n for n in (RULES_FILE, PROJECTS_FILE, CORRECTIONS_FILE, CACHE_FILE, "places.json")]
    out = {}
    for path in files:
        found = file_stamp(stored(path))
        out[path.relative_to(vault.root).as_posix()] = list(found) if found is not None else None
    return out

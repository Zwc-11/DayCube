"""Synthetic days for the day timeline: a script of what someone did, written the way the receiver
and the phone record it, then scored second by second against the timeline built from those
records. ``eval/day_sim.py`` runs many days; the tests run a few.

A day (``script``) runs from the morning to past midnight: desk work on one task at a time
(several apps and pages, glances elsewhere for a few seconds), breaks (idle, locked, the lid
closed, recording paused), a video watched without touching anything, a page read for minutes
without input, and on phone days outings to a cafe with a voice memo. ``record`` writes what each
block leaves behind: ``windows.jsonl`` as ``capture/screen.py`` writes it, ``browser.jsonl`` as
the extension does, and the phone's ``day.json``, transits and moments.

The truth of each second is ``(where, category, project)``, or nothing when nothing recorded it,
decided from the script alone: at the desk, the task's label (a glance belongs to the task it
interrupts); otherwise the phone's place or transit when there is one; otherwise away while the
computer was recording. Breaks last six minutes or more: the screen reader cannot tell a shorter
one from sitting still, a limit of the capture rather than of the timeline.

``noise`` adds what real capture has: the screen reader's half-second polling, the extension
reporting a little late, records written twice, a torn line, a pass over the bare desktop between
two windows, and phone visit boundaries off by up to 15 s. Everything written is marked synthetic
(``"synthetic": true`` on capture records, ``source.kind: "synthetic"`` on phone records).
"""

from __future__ import annotations

import json
import math
import random
import statistics
import uuid
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..time import instant
from ..vault import Record, Vault
from .timeline import window

ZONE = "America/Toronto"
BROWSER = "opera.exe"
PROJECTS_YAML = """projects:
  DayCube:
    folders: ['E:\\DAYCUBE']
"""


@dataclass(frozen=True)
class Thing:
    """Something that can be in front: an app's window, or a page (``url``)."""

    program: str
    title: str
    url: str = ""
    private: bool = False  # an app the reader never names the window of
    excluded: bool = False  # a page the extension does not record


def page(url: str, title: str, *, excluded: bool = False) -> Thing:
    return Thing(BROWSER, title, url, excluded=excluded)


def app(program: str, title: str, *, private: bool = False) -> Thing:
    return Thing(program, title, private=private)


Label = tuple[str, str | None, str | None]  # (where, category, project)

# What a person works on, and what is in front while they do: the expected labels are the point.
TASKS: dict[tuple[str, str | None], list[Thing]] = {
    ("coding", "DayCube"): [
        app("code.exe", "timeline.py - DAYCUBE - Visual Studio Code"),
        app("code.exe", "● labels.py - DAYCUBE - Visual Studio Code"),
        app("windowsterminal.exe", "E:\\DAYCUBE"),
        page(
            "https://github.com/caesar/daycube/pull/12",
            "Add the day timeline by caesar · Pull Request #12 · caesar/daycube",
        ),
    ],
    ("studying", "CS 136"): [
        page("https://learn.uwaterloo.ca/d2l/le/content/123/viewContent/9", "CS 136 Assignment 3 - Hash tables"),
        page("https://piazza.com/class/abc", "CS 136 - Piazza"),
        app("sumatrapdf.exe", "CS136-A3.pdf - SumatraPDF"),
    ],
    ("reading", None): [
        page("https://en.wikipedia.org/wiki/Hash_table", "Hash table - Wikipedia"),
        page("https://arxiv.org/abs/1706.03762", "[1706.03762] Attention Is All You Need"),
        app("acrobat.exe", "paper.pdf - Adobe Acrobat Reader"),
    ],
    ("ai", None): [app("claude.exe", "Claude"), page("https://chatgpt.com/c/1", "ChatGPT")],
    ("messaging", None): [
        page("https://mail.google.com/mail/u/0/#inbox", "Inbox (3) - caesar@example.com - Gmail"),
        app("discord.exe", "#general | Hack Club - Discord"),
        app("weixin.exe", "微信"),
    ],
    ("writing", None): [
        app("winword.exe", "Essay.docx - Word"),
        page("https://docs.google.com/document/d/x/edit", "Essay outline - Google Docs"),
    ],
    ("career", None): [
        page("https://jobs.ashbyhq.com/example/1", "Software Engineer Intern - Example"),
        page("https://app.joinhandshake.com/stu/postings", "Handshake"),
    ],
    ("social", None): [
        page("https://x.com/home", "Home / X"),
        page("https://www.reddit.com/r/uwaterloo/", "r/uwaterloo"),
    ],
    ("watching", None): [page("https://www.youtube.com/watch?v=KyUTuwz_b7Q", "Hash Tables - Computerphile - YouTube")],
    ("listening", None): [app("spotify.exe", "Spotify Premium")],
    ("meetings", None): [app("zoom.exe", "Zoom Meeting")],
    ("admin", None): [app("explorer.exe", "Downloads - File Explorer"), app("1password.exe", "", private=True)],
    ("shopping", None): [page("https://www.amazon.ca/dp/B0", "Amazon.ca: USB-C cable")],
    ("other", None): [app("mysteryapp.exe", "Mystery"), page("https://zz-unknown.example/", "Some page")],
    ("private", None): [page("https://bank.example/", "", excluded=True)],
}
VIDEOS = [
    (page("https://www.youtube.com/watch?v=KyUTuwz_b7Q", "Hash Tables - Computerphile - YouTube"), ("watching", None)),
    (page("https://www.youtube.com/watch?v=cs136l12", "CS 136 Lecture 12 - YouTube"), ("studying", "CS 136")),
]
READ = (page("https://en.wikipedia.org/wiki/Hash_table", "Hash table - Wikipedia"), ("reading", None))
EVERYTHING = [thing for things in TASKS.values() for thing in things]


@dataclass
class Block:
    t0: float
    t1: float
    kind: str  # desk, watch, read, idle, locked, outing, asleep, paused, night
    label: tuple[str, str | None] | None = None  # at the desk: (category, project)
    parts: list[tuple[float, float, Thing]] = field(default_factory=list)  # what was in front, when
    glances: int = 0


@dataclass
class Visit:
    t0: float
    t1: float
    place: str  # place id
    name: str
    kind: str  # house, cafe; or "transit"


@dataclass
class Memo:
    t0: float
    seconds: float
    moment: str


@dataclass
class Day:
    day: str
    zone: str
    blocks: list[Block]
    visits: list[Visit]  # the phone's places and transits, true times
    memos: list[Memo]
    morning: float  # when the computer is used again the next morning
    morning_thing: Thing


# -- the script --------------------------------------------------------------------------------


def _at(day: str, zone: ZoneInfo, hour: float) -> float:
    first = date.fromisoformat(day)
    return (datetime(first.year, first.month, first.day, tzinfo=zone) + timedelta(hours=hour)).timestamp()


def _desk(rng: random.Random, t0: float, t1: float, label: tuple[str, str | None]) -> Block:
    things = TASKS[label]
    parts: list[tuple[float, float, Thing]] = []
    cursor, last = t0, None
    while cursor < t1:
        thing = rng.choice([t for t in things if t != last] or things)
        end = min(t1, cursor + rng.uniform(30, 600))
        if t1 - end < 30:
            end = t1
        parts.append((cursor, end, thing))
        cursor, last = end, thing
    out: list[tuple[float, float, Thing]] = []
    glances = 0
    for a, b, thing in parts:  # glances elsewhere, well inside a part
        if b - a >= 150 and rng.random() < 0.5:
            g0 = a + rng.uniform(60, b - a - 75)
            g1 = g0 + rng.uniform(3, 15)
            other = rng.choice([t for t in EVERYTHING if t not in things])
            out += [(a, g0, thing), (g0, g1, other), (g1, b, thing)]
            glances += 1
        else:
            out.append((a, b, thing))
    return Block(t0, t1, "desk", label, out, glances)


def script(day: str, seed: int, *, phone: bool) -> Day:
    """One day, from about 9:00 to between 23:00 and 01:30."""
    rng = random.Random(seed)
    zone = ZoneInfo(ZONE)
    t = _at(day, zone, rng.uniform(8.5, 10.0))
    end = _at(day, zone, rng.uniform(23.0, 25.5))
    blocks: list[Block] = []
    outings = 0
    last_task = None
    while end - t >= 600:  # every block at least ten minutes, but a read
        previous = blocks[-1].kind if blocks else "away"
        hour = (
            datetime.fromtimestamp(t, zone) - datetime.fromtimestamp(_at(day, zone, 0), zone)
        ).total_seconds() / 3600
        choices = ["desk"] * 12 + ["watch", "read"] if previous not in ("desk", "watch", "read") else (
            ["desk"] * 10 + ["idle"] * 2 + ["locked", "asleep", "paused", "watch", "read"]
        )  # fmt: skip
        if phone and outings < 2 and 11 <= hour <= 20 and previous == "desk":
            choices += ["outing"] * 2
        kind = rng.choice(choices)
        if kind == "desk":
            label = rng.choice([k for k in TASKS if k != last_task])
            last_task = label
            blocks.append(_desk(rng, t, min(end, t + rng.uniform(8, 75) * 60), label))
        elif kind == "watch":
            video, label = rng.choice(VIDEOS)
            t1 = min(end, t + rng.uniform(8, 40) * 60)
            blocks.append(Block(t, t1, "watch", label, [(t, t1, video)]))
            last_task = label
        elif kind == "read":
            thing, label = READ
            t1 = min(end, t + rng.uniform(150, 280))
            blocks.append(Block(t, t1, "read", label, [(t, t1, thing)]))
            last_task = label
        else:
            minutes = {"idle": (6, 45), "locked": (8, 60), "asleep": (10, 120), "paused": (5, 30), "outing": (45, 90)}
            blocks.append(Block(t, min(end, t + rng.uniform(*minutes[kind]) * 60), kind))
            outings += kind == "outing"
        t = blocks[-1].t1
    morning = _at(day, zone, 24 + 8.75)
    blocks.append(Block(t, morning, "night"))
    visits: list[Visit] = []
    memos: list[Memo] = []
    if phone:
        home_from, n = _at(day, zone, 7.0), 0
        for block in blocks:
            if block.kind != "outing":
                continue
            walk = rng.uniform(6, 12) * 60
            leave, back = block.t0 + 60, block.t1 - 60
            visits.append(Visit(home_from, leave, f"ph-home-{n}", "Home", "house"))
            visits.append(Visit(leave, leave + walk, f"ph-walk-{n}a", "Walked 0.6 km", "transit"))
            visits.append(Visit(leave + walk, back - walk, f"ph-cafe-{n}", "Maple Cafe", "cafe"))
            visits.append(Visit(back - walk, back, f"ph-walk-{n}b", "Walked 0.6 km", "transit"))
            memo = leave + walk + rng.uniform(5, 15) * 60
            memos.append(Memo(memo, rng.uniform(60, 300), f"mo_voice_{n}"))
            home_from, n = back, n + 1
        visits.append(Visit(home_from, morning + 1800, f"ph-home-{n}", "Home", "house"))
    morning_thing = TASKS[("coding", "DayCube")][0]
    return Day(day, ZONE, blocks, visits, memos, morning, morning_thing)


# -- what it leaves behind ---------------------------------------------------------------------


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat()


class _Writer:
    """Records in the order things happen. With noise, the screen reader sees a change at its next
    round (every 0.5 s, from a phase of its own) and the extension reports 50-300 ms late; each
    keeps its own order, as a single thread logging does."""

    def __init__(self, rng: random.Random, noise: bool) -> None:
        self.rng, self.noise = rng, noise
        self.windows: list[Record] = []
        self.browser: list[Record] = []
        self.tabs: dict[str, int] = {}
        self.phase = rng.uniform(0, 0.5) if noise else 0.0
        self.last_ext = 0.0

    def _jitter(self, low: float, high: float) -> float:
        return self.rng.uniform(low, high) if self.noise else 0.0

    def _id(self) -> str:
        return uuid.UUID(int=self.rng.getrandbits(128)).hex

    def window(self, t: float, **fields: object) -> None:
        if self.noise:
            t = self.phase + math.ceil((t - self.phase) / 0.5) * 0.5  # the reader's next round
        self.windows.append({"id": self._id(), "t": _iso(t), **fields, "synthetic": True})

    def ext(self, t: float, kind: str, **fields: object) -> None:
        if self.noise:
            t = self.last_ext = max(self.last_ext, t + self.rng.uniform(0.05, 0.3))
        self.browser.append({"id": self._id(), "t": _iso(t), "kind": kind, **fields, "synthetic": True})

    def front(self, t: float, thing: Thing, before: Thing | None, *, force: bool = False) -> None:
        """``thing`` comes to front at ``t``, from ``before``: the reader's record, and the
        extension's when the browser's focus or tab changes."""
        if thing == before and not force:
            return
        if self.noise and before is not None and self.rng.random() < 0.03:
            self.window(t - self.rng.uniform(0.5, 2.0), kind="away", why="no window")  # a pass over the desktop
        if thing.url:
            self.window(t, kind="window", app=BROWSER, browser=True)
        elif thing.private:
            self.window(t, kind="window", app=thing.program, private=True)
        else:
            url = f"app://{quote(thing.program, safe='')}/{quote(thing.title, safe='')}"
            self.window(t, kind="window", app=thing.program, title=thing.title, url=url)
        if thing == before:
            return
        if thing.url:
            tab = self.tabs.setdefault(thing.url, len(self.tabs) + 1)
            fields = {} if thing.excluded else {"url": thing.url, "title": thing.title}
            self.ext(t, "focus", tab=tab, window=1, **fields)
        elif before is not None and before.url:
            self.ext(t, "focus", tab=None)


def record(vault: Vault, day: Day, seed: int, *, noise: bool) -> None:
    """Write the day's capture logs and phone records into ``vault``."""
    rng = random.Random(seed * 7919 + 1)
    w = _Writer(rng, noise)
    zone = ZoneInfo(day.zone)
    current: Thing | None = None
    away = True  # the night before: the reader logs the window when the day starts
    for block in day.blocks:
        if block.kind in ("desk", "watch", "read"):
            for index, (a, _, thing) in enumerate(block.parts):
                w.front(a, thing, current, force=away and index == 0)
                if away and index == 0:
                    w.ext(a, "idle", state="active")
                away, current = False, thing
            if block.kind == "watch":  # play, then no input until the video is paused
                video = block.parts[0][2]
                tab = w.tabs[video.url]
                start = block.t0 + 2.0
                w.ext(start, "media", tab=tab, window=1, url=video.url, title=video.title, state="playing",
                      position_s=0.0, duration_s=3600.0)  # fmt: skip
                w.ext(start + 60, "idle", state="idle")
                if block.t1 - start > 300:
                    w.window(start + 300, kind="away", why="idle", since=_iso(start))
                w.ext(block.t1, "media", tab=tab, window=1, url=video.url, title=video.title, state="paused",
                      position_s=block.t1 - start, duration_s=3600.0)  # fmt: skip
                w.ext(block.t1, "idle", state="active")
                w.front(block.t1, video, video, force=True)  # the reader logs the window on return
            elif block.kind == "read":
                w.ext(block.t0 + 60, "idle", state="idle")
                w.ext(block.t1, "idle", state="active")
        elif block.kind == "idle":
            w.ext(block.t0 + 60, "idle", state="idle")
            w.window(block.t0 + 300, kind="away", why="idle", since=_iso(block.t0))
            away = True
        elif block.kind in ("locked", "outing", "night"):
            w.window(block.t0, kind="away", why="locked")
            w.ext(block.t0 + 1, "idle", state="locked")
            away = True
        elif block.kind == "asleep":
            wake = block.t1 - 20
            w.window(wake, kind="away", why="asleep", since=_iso(block.t0 + w._jitter(0, 0.5)))
            w.window(wake + 1, kind="away", why="locked")
            away = True
        elif block.kind == "paused":
            w.window(block.t0, kind="away", why="paused")
            away = True
    w.front(day.morning, day.morning_thing, current, force=True)
    quiet = [(b.t0, b.t1) for b in day.blocks if b.kind in ("paused", "asleep")]
    t = day.blocks[0].t0 + 1.0
    while t < day.morning:  # the reader's "still" every five minutes, while it records
        if not any(a <= t < b for a, b in quiet):
            w.window(t, kind="still")
        t += 300.0
    if noise:  # about one record in a hundred written twice, as a retried append writes it
        w.windows = [copy for item in w.windows for copy in ([item, dict(item)] if rng.random() < 0.01 else [item])]
        w.browser = [copy for item in w.browser for copy in ([item, dict(item)] if rng.random() < 0.01 else [item])]
    by_day: dict[tuple[str, str], list[Record]] = {}
    for name, log in (("windows.jsonl", w.windows), ("browser.jsonl", w.browser)):
        for item in sorted(log, key=lambda r: instant(r["t"])):  # stable: one round's records keep their order
            local = instant(item["t"]).astimezone(zone).date().isoformat()
            by_day.setdefault((local, name), []).append(item)
    for (local, name), items in by_day.items():
        lines = [json.dumps(item, ensure_ascii=False) for item in items]
        if noise:
            lines.insert(rng.randrange(len(lines) + 1), '{"id": "torn", "t": "2026-')  # a torn append
        folder = vault.root / "capture" / local
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if day.visits:
        _phone(vault, day, rng, noise)


def _phone(vault: Vault, day: Day, rng: random.Random, noise: bool) -> None:
    zone = ZoneInfo(day.zone)

    def local(t: float) -> str:
        return datetime.fromtimestamp(t, zone).isoformat()

    cuts = {}
    for visit in day.visits:  # one jitter per boundary, so neighbouring visits still meet
        for t in (visit.t0, visit.t1):
            cuts.setdefault(t, t + (rng.uniform(-15, 15) if noise else 0.0))
    places, events = [], []
    for index, visit in enumerate(day.visits):
        t0, t1 = cuts[visit.t0], cuts[visit.t1]
        source = {"kind": "synthetic", "ref": "simulate:phone"}
        if visit.kind == "transit":
            events.append({"id": visit.place.replace("-", "_"), "t": local(t0), "t_end": local(t1), "type": "place.transit",
                           "label": visit.name, "place": None, "source": source, "confidence": 1.0,
                           "fidelity": "estimated", "from": "", "to": "", "mode": "walk"})  # fmt: skip
            continue
        places.append({"id": visit.place, "name": visit.name, "kind": visit.kind, "t0": local(t0), "t1": local(t1),
                       "lat": 43.47, "lon": -80.54, "osm_id": None, "venue": None, "floors_visited": [0],
                       "conf": 0.9, "layer_index": index})  # fmt: skip
    folder = vault.day_path(day.day, must_exist=False)
    for memo in day.memos:
        events.append({"id": f"ph_{memo.moment}", "t": local(memo.t0), "t_end": local(memo.t0 + memo.seconds),
                       "type": "moment.audio", "label": "Voice memo", "place": None, "source": {"kind": "synthetic",
                       "ref": "simulate:memo"}, "confidence": 0.9, "fidelity": "known", "moment_id": memo.moment})  # fmt: skip
        moment = folder / "moments" / memo.moment
        moment.mkdir(parents=True, exist_ok=True)
        meta = {"id": memo.moment, "title": "Voice memo", "t0": local(memo.t0), "duration_s": memo.seconds,
                "place": None, "camera": {"fov_deg": 60.0}, "fidelity": "known", "media_available": False}  # fmt: skip
        (moment / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        transcript = {"speaker_map": {}, "segments": [{"start": 1.0, "end": 4.0, "text": "A synthetic line.",
                                                       "speaker": None, "words": []}]}  # fmt: skip
        (moment / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    data = {"schema": "daycube.day/1", "date": day.day, "tz": day.zone, "places": places, "transits": [],
            "cube_faces": [], "stats": {"events": len(events), "moments": len(day.memos), "places": len(places),
                                        "floor_changes": 0}}  # fmt: skip
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "day.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    events.sort(key=lambda e: instant(e["t"]))
    (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    # The rest of a vault day, empty, so the day validates and indexes like an imported one.
    (folder / "skeleton.geojson").write_text('{"type": "FeatureCollection", "features": []}\n', encoding="utf-8")
    (folder / "graph.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
    (folder / "screens").mkdir(exist_ok=True)
    (folder / "screens" / "ocr.jsonl").write_text("", encoding="utf-8")


# -- the truth, and the score ------------------------------------------------------------------


def truth(day: Day) -> list[tuple[float, float, Label]]:
    """The true label of the day, as disjoint intervals in time order; gaps are not recorded."""
    pc: list[tuple[float, float, Label | None]] = []
    for block in day.blocks:
        if block.label is not None:
            pc.append((block.t0, block.t1, ("desk", *block.label)))
        elif block.kind == "paused":
            pc.append((block.t0, block.t1, None))
        else:
            pc.append((block.t0, block.t1, ("away", None, None)))
    phone = [(v.t0, v.t1, ("away", None, None) if v.kind == "transit" else (f"place:{v.place}", None, None))
             for v in day.visits]  # fmt: skip
    cuts = sorted({t for a, b, _ in pc + phone for t in (a, b)})
    out: list[tuple[float, float, Label]] = []
    for a, b in zip(cuts, cuts[1:], strict=False):
        mid = (a + b) / 2
        at_pc = next((lab for x, y, lab in pc if x <= mid < y), "none")
        at_phone = next((lab for x, y, lab in phone if x <= mid < y), None)
        label = at_pc if at_pc not in ("none", None) and at_pc[0] == "desk" else at_phone or (
            at_pc if at_pc != "none" else None
        )  # fmt: skip
        if label is None:
            continue
        if out and out[-1][2] == label and out[-1][1] == a:
            out[-1] = (out[-1][0], b, label)
        else:
            out.append((a, b, label))
    return out


def _lookup(spans: list[tuple[float, float, Label]], starts: list[float], t: float) -> Label | None:
    index = bisect_right(starts, t) - 1
    if index >= 0 and spans[index][0] <= t < spans[index][1]:
        return spans[index][2]
    return None


def score(day: Day, segments: list[Record]) -> Record:
    """Seconds right, boundary errors, switches and talk, of a timeline against the day's truth."""
    zone = ZoneInfo(day.zone)
    start, stop = (int(t.timestamp()) for t in window(day.day, zone))
    real = [(max(a, start), min(b, stop), lab) for a, b, lab in truth(day) if b > start and a < stop]
    built = [(instant(s["t"]).timestamp(), instant(s["until"]).timestamp(), (s["where"], s["category"], s["project"]))
             for s in segments]  # fmt: skip
    real_starts, built_starts = [a for a, _, _ in real], [a for a, _, _ in built]
    seconds = right = desk = desk_right = 0
    wrong: Counter[tuple] = Counter()
    for second in range(start, stop):
        t = second + 0.5
        expected, found = _lookup(real, real_starts, t), _lookup(built, built_starts, t)
        if expected is None and found is None:
            continue
        seconds += 1
        right += expected == found
        if expected is not None and expected[0] == "desk":
            desk += 1
            desk_right += expected == found
        if expected != found:
            wrong[(expected, found)] += 1
    edges = []
    for (a, b, lab), (c, _, nxt) in zip(real, real[1:] + [(None, None, None)], strict=True):
        edges.append(a)
        if c is None or c != b or nxt != lab:
            edges.append(b)
    edges = sorted(set(edges) - {start, stop})
    marks = sorted({t for a, b, _ in built for t in (a, b)})
    errors = []
    for edge in edges:
        index = bisect_right(marks, edge)
        near = [abs(edge - marks[i]) for i in (index - 1, index) if 0 <= i < len(marks)]
        errors.append(min(near) if near else float("inf"))
    talk_ok = 0
    for memo in day.memos:
        expected = _lookup(real, real_starts, memo.t0 + memo.seconds / 2)
        holder = next((s for s in segments if memo.moment in s["talk"]), None)
        talk_ok += holder is not None and (holder["where"], holder["category"], holder["project"]) == expected
    return {
        "day": day.day,
        "seconds": seconds,
        "accuracy": right / seconds if seconds else 1.0,
        "desk_accuracy": desk_right / desk if desk else 1.0,
        "boundaries": len(edges),
        "median_boundary_s": statistics.median(errors) if errors else 0.0,
        "p90_boundary_s": sorted(errors)[int(0.9 * (len(errors) - 1))] if errors else 0.0,
        "max_boundary_s": max(errors, default=0.0),
        "switches_true": sum(b.glances for b in day.blocks),
        "switches_found": sum(s["switches"] for s in segments),
        "talk": [talk_ok, len(day.memos)],
        "wrong": [[list(map(str, k)), n] for k, n in wrong.most_common(5)],
    }


def simulate(root: Path, day: str, seed: int, *, noise: bool, phone: bool) -> tuple[Day, Vault]:
    """A fresh vault under ``root`` with one simulated day recorded in it."""
    vault = Vault(root)
    vault.initialize()
    (vault.root / "projects.yaml").write_text(PROJECTS_YAML, encoding="utf-8")
    scripted = script(day, seed, phone=phone)
    record(vault, scripted, seed, noise=noise)
    return scripted, vault

"""A day cut into episodes (``vault/<date>/episodes.json``).

An episode is a stretch of the day's timeline (ingest/review/timeline.py) that stayed with one
thing: the same project (or, without one, the same kind of activity) at the desk, one place, one
conversation. A short interruption (a glance at another window, a few minutes away) does not end
it. Time away, and browsing the extension does not record, are not episodes.

Each episode carries four signals between 0 and 1, from records the vault already holds:

    attention    reading the eye tracker measured (lines read, read again, long looks) or the
                 share of the time with typing or clicking, whichever is higher; lowered by
                 switching windows
    feeling      how far the nearest feeling report (ingest/feeling.py) was from neutral
    novelty      the share of its apps, sites and places not met in the 30 days before (0 until
                 there is a week of episodes to judge against)
    importance   what was copied or searched from it, and photos, scans or recordings made in it

Each also keeps up to five highlights: what the records say happened in it (searches, the lines
read most closely, a text copied), shown when a question about it is answered.

How strong an episode is now is not kept here: it follows from these signals, the weights in use
and the recalls made since (history.py). The file is made from the day's timeline and
records alone, so it can be made again at any time; it is written only when it changes.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import feeling
from ..review import timeline
from ..review.metrics import units_in
from ..review.signals import inputs, overlap
from ..review.summary import day_zone
from ..store import LifeStore
from ..vault import Record, Vault, read_json, stored
from .strength import Signals

FILE = "episodes.json"
# Chosen on ten recorded days (docs/recall.md): 5 and 3 minutes cut them into 12 to 75 pieces of
# 5 to 10 minutes (the median length); these give 6 to 35 of 9 to 21 minutes.
BRIEF_S = 600.0  # an interruption shorter than this does not end an episode
MIN_S = 300.0  # a stretch shorter than this is not an episode
NOVELTY_DAYS = 30
NOVELTY_MIN_DAYS = 7  # days of episodes before something can be called new
FEELING_REACH_S = 2700.0  # a report this near an episode speaks for it
READ, STARE, COPY, SEARCH = "web.read", "web.dwell", "web.selection", "web.search"
MADE = ("moment.audio", "moment.video", "moment.photo", "moment.scan")
TITLE_MAX = 90
HIGHLIGHTS = (("search", SEARCH, 2), ("read", READ, 2), ("copy", COPY, 1))  # kind, unit type, at most
HIGHLIGHT_MAX = 160


def _context(segment: Record) -> tuple[str, str] | None:
    """What a segment is about, or None when nothing of it was recorded: time away, or a page
    the browser extension does not record."""
    where = segment["where"]
    if segment.get("talk"):
        return ("talk", where)
    if where == "desk":
        about = segment.get("project") or segment.get("category") or "other"
        return None if about == timeline.PRIVATE else ("desk", about)
    return ("place", where) if where.startswith("place:") else None


def cut(segments: list[Record]) -> list[list[Record]]:
    """Segments grouped into episodes: runs of one context, a brief interruption inside a run kept in it."""
    spans = [
        (datetime.fromisoformat(s["t"]).timestamp(), datetime.fromisoformat(s["until"]).timestamp()) for s in segments
    ]
    groups: list[tuple[tuple[str, str], list[int]]] = []
    for i, segment in enumerate(segments):
        context = _context(segment)
        if context is None:
            continue
        if groups and groups[-1][0] == context and spans[i][0] - spans[groups[-1][1][-1]][1] <= BRIEF_S:
            groups[-1][1].append(i)
        elif (
            len(groups) >= 2
            and groups[-2][0] == context
            and spans[groups[-1][1][-1]][1] - spans[groups[-1][1][0]][0] <= BRIEF_S
            and spans[i][0] - spans[groups[-2][1][-1]][1] <= 2 * BRIEF_S
        ):
            # A, a brief B, A again: one episode of A, the interruption inside it.
            interruption = groups.pop()[1]
            groups[-1][1].extend([*interruption, i])
        else:
            groups.append((context, [i]))
    long_enough = [g for _, g in groups if spans[g[-1]][1] - spans[g[0]][0] >= MIN_S]
    return [[segments[i] for i in group] for group in long_enough]


def _signals(units: list[Record], minutes: float, active_s: float, switches: int, reports: list[Record],
             keys: set[str], known: set[str] | None, t0: float, t1: float) -> Signals:  # fmt: skip
    """``active_s``: seconds of the episode with typing or clicking; ``known``: what the days
    before held, or None when they are too few to call anything new."""
    kinds = Counter(u["type"] for u in units)
    read = [u["text"] for u in units if u["type"] == READ]
    again = len(read) - len(set(read))
    reading = 1 - math.exp(-(len(read) + 2 * again + kinds[STARE]) / 20)
    working = min(1.0, active_s / max(minutes * 60, 1.0))
    attention = max(reading, working) * (1 - min(0.5, switches / max(minutes, 1.0) / 4))
    near = [
        r for r in reports if t0 - FEELING_REACH_S <= datetime.fromisoformat(r["t"]).timestamp() <= t1 + FEELING_REACH_S
    ]
    felt = max((max(abs(r["energy"] - 3), abs(r["mood"] - 3)) / 2 for r in near), default=0.0)
    novelty = len(keys - known) / len(keys) if keys and known is not None else 0.0
    importance = 1 - math.exp(-(kinds[COPY] + kinds[SEARCH] + 3 * sum(kinds[k] for k in MADE)) / 3)
    return Signals(round(attention, 3), round(felt, 3), round(novelty, 3), round(importance, 3))


def _highlights(units: list[Record]) -> list[Record]:
    """What the records say happened in an episode, in the units' own words: its first searches,
    the lines read most closely, a text copied. No text twice."""
    out, seen = [], set()
    for kind, unit_type, most in HIGHLIGHTS:
        found = [u for u in units if u["type"] == unit_type]
        if unit_type == READ:
            found.sort(key=lambda u: -(u["attention"] or 0.0))
        texts = dict.fromkeys(" ".join(u["text"].split())[:HIGHLIGHT_MAX] for u in found)
        fresh = [text for text in texts if text and text.casefold() not in seen][:most]
        seen.update(text.casefold() for text in fresh)
        out += [{"kind": kind, "text": text} for text in fresh]
    return out


def _title(group: list[Record], context: tuple[str, str]) -> str:
    """What the episode was, in the timeline's own words: the project or activity and the window
    longest in front, the place's name, or the conversation's."""
    longest = max(group, key=lambda s: datetime.fromisoformat(s["until"]) - datetime.fromisoformat(s["t"]))
    doing = longest.get("doing") or ""
    lead = {"desk": context[1], "talk": "Conversation"}.get(context[0])
    return (f"{lead}: {doing}".rstrip(": ") if lead else doing or "A place")[:TITLE_MAX]


def _known(vault: Vault, day: str) -> set[str] | None:
    """The apps, sites and places of the episodes of the NOVELTY_DAYS days before ``day``; None
    when fewer than NOVELTY_MIN_DAYS of them have episodes."""
    known: set[str] = set()
    first, days = date.fromisoformat(day), 0
    for back in range(1, NOVELTY_DAYS + 1):
        found = read(vault, (first - timedelta(days=back)).isoformat())
        days += bool(found)
        for episode in found or []:
            known.update(episode["keys"])
    return known if days >= NOVELTY_MIN_DAYS else None


def build(vault: Vault, store: LifeStore, day: str, zone: str) -> list[Record]:
    """The episodes of review day ``day`` from its written timeline (none: no episodes).
    ``zone``: the vault's, used unless the day was recorded in another."""
    segments = timeline.read(vault, day)
    if not segments:
        return []
    start, end = timeline.window(day, ZoneInfo(day_zone(vault, day, zone)))
    units = units_in(store, start, end, (READ, STARE, COPY, SEARCH, *MADE))
    days = [d.isoformat() for d in (start.date(), end.date())]
    reports = [r for d in days for r in feeling.reports(vault, d)]
    busy = [b for b in inputs(vault, days, start, end) if b.keys or b.clicks]
    known = _known(vault, day)
    out = []
    for group in cut(segments):
        context = _context(group[0]) or ("place", "")
        t0, t1 = datetime.fromisoformat(group[0]["t"]), datetime.fromisoformat(group[-1]["until"])
        inside = [u for u in units if t0.timestamp() <= u["ts"] < t1.timestamp()]
        keys = {s["broad"] for s in group if s.get("broad")} | {
            s["where"] for s in group if s["where"].startswith("place:")
        }
        minutes = (t1 - t0).total_seconds() / 60
        active_s = sum(overlap(b.t0, b.t1, t0.timestamp(), t1.timestamp()) for b in busy)
        signals = _signals(inside, minutes, active_s, sum(s.get("switches") or 0 for s in group), reports, keys,
                           known, t0.timestamp(), t1.timestamp())  # fmt: skip
        out.append({
            "id": f"ep_{t0:%Y%m%d_%H%M}",
            "t": group[0]["t"],
            "until": group[-1]["until"],
            "kind": context[0],
            "where": group[0]["where"],
            "title": _title(group, context),
            "category": next((s["category"] for s in group if s.get("category")), None),
            "project": next((s["project"] for s in group if s.get("project")), None),
            "talk": sorted({m for s in group for m in s.get("talk") or []}),
            "minutes": round(minutes, 1),
            "keys": sorted(keys),
            "signals": asdict(signals),
            "highlights": _highlights(inside),
            "evidence": [e for s in group for e in s.get("evidence") or []][:40],
            "fidelity": "known" if all(s.get("fidelity") == "known" for s in group) else "estimated",
        })  # fmt: skip
    return out


def write(vault: Vault, store: LifeStore, day: str, zone: str) -> list[Record]:
    """Write ``day``'s episodes if they changed; returns them."""
    episodes = build(vault, store, day, zone)
    path = vault.day_path(day, must_exist=False) / FILE
    if episodes != (read(vault, day) or []) or (episodes and not stored(path).exists()):
        path.parent.mkdir(parents=True, exist_ok=True)
        vault.write_json(path, {"date": day, "episodes": episodes})
    return episodes


def read(vault: Vault, day: str) -> list[Record] | None:
    """The episodes written for ``day``, or None when there is no file."""
    path = vault.day_path(day, must_exist=False) / FILE
    return read_json(path)["episodes"] if stored(path).exists() else None

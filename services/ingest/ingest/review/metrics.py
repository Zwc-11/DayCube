"""The numbers of a review day, all computed here, and the records each was counted from.

From the day's timeline (``timeline.py``): time at the desk, away and at places, by category and
by project, the first and last desk time, the longest stretch on one thing, stretches of 25
minutes or more, switches, desk time after midnight, and the share no category knew. From the
search units in the vault database (``ingest.store``): lines read, searches, copies,
conversations. From the knowledge graph: deadlines in the week after the day. From the capture logs
beside the timeline (``signals.py``): typing and mouse counts, the lines you noted, phone apps; and
your commits (``git.py``).

Every number has the forms it may be written in (``forms``), so the validator (``summary.py``)
can tell a number taken from here from one made up. Every record a review may cite is in
``evidence``, keyed by its id: a timeline segment as ``timeline:<n>`` (its own records listed
with it), a unit by its evidence id. The facts hash (``digest``) covers everything a review is
written from: the same facts are never written twice.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from ..capture.synthesize import app_name
from ..store import LifeStore
from ..time import instant
from ..vault import Record, Vault
from . import git, plan, signals, sites
from .labels import OTHER
from .timeline import PRIVATE, Built, window

FOCUS_S = 25 * 60.0  # a stretch on one thing this long or longer is a focus block
DONE_MIN_S = 10 * 60.0  # a project with this much time is listed as worked on
READ_PAGES_MIN = 3  # passages read on a page for it to be listed as read
TEXT_LIMIT = 220  # characters of a record's text in the evidence table
UNIT_TYPES = ("web.read", "web.search", "web.selection", "web.dwell", "web.media", "said.quote", "moment.audio",
              "moment.video", "place.visit")  # fmt: skip
CITED_PER_TYPE = 20  # units of one type a review may cite (the most attended first)
SEGMENTS_CITED = 40  # the longest timeline segments a review may cite
DEADLINE_DAYS = 7  # deadlines due within this many days after the review day
DISTRACTING = ("social", "watching", "games", "shopping", "news")
APPS_SHOWN = 8  # phone apps listed with their time


def normal(token: str) -> str | None:
    """A number as written, in one form: "09:05" -> "9:05", "1,234" -> "1234", "6.50" -> "6.5"."""
    token = token.strip().rstrip(".,")
    if ":" in token:
        hours, _, minutes = token.partition(":")
        if hours.isdigit() and minutes[:2].isdigit():
            return f"{int(hours)}:{minutes[:2]}"
        return None
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", token):
        token = token.replace(",", "")  # thousands
    try:
        value = Decimal(token.replace(",", "."))  # a decimal comma
    except InvalidOperation:
        return None
    text = format(value.normalize(), "f")
    return text if "." in text else str(int(value))


def duration(seconds: float) -> str:
    """"45 s", "12 min", "1 h 5 min", "3 h"."""
    if seconds < 60:
        return f"{round(seconds)} s"
    minutes = round(seconds / 60)
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h" if not rest else f"{hours} h {rest} min"


def duration_forms(seconds: float) -> set[str]:
    """"2 h 5 min", "125 min", "2.1 hours", "about 2 hours", "125" written any of those ways."""
    minutes = round(seconds / 60)
    hours = seconds / 3600
    forms = {str(minutes), str(minutes // 60), str(minutes % 60), str(round(seconds)), str(round(hours)), str(int(hours))}
    forms |= {normal(f"{hours:.1f}"), normal(f"{hours:.2f}")}  # type: ignore[arg-type]
    return {f for f in forms if f}


def share_forms(share: float) -> set[str]:
    return {f for f in (str(round(share * 100)), normal(f"{share * 100:.1f}")) if f}


def clock(moment: datetime) -> str:
    return f"{moment:%H:%M}"


def clock_forms(moment: datetime) -> set[str]:
    hour12 = moment.hour % 12 or 12
    return {f"{moment.hour}:{moment.minute:02d}", f"{hour12}:{moment.minute:02d}", str(moment.hour), str(hour12)}


@dataclass
class Number:
    key: str
    label: str
    value: float | None
    display: str
    evidence: list[str]
    forms: set[str] = field(default_factory=set)
    parts: dict = field(default_factory=dict)  # what the template words it with

    def record(self) -> Record:
        return {"key": self.key, "label": self.label, "display": self.display, "value": self.value,
                "evidence": self.evidence}  # fmt: skip


@dataclass
class Share:
    name: str
    seconds: float
    share: float
    evidence: list[str]

    @property
    def display(self) -> str:
        return f"{duration(self.seconds)} ({round(self.share * 100)}%)"

    def record(self) -> Record:
        return {"name": self.name, "seconds": round(self.seconds, 1), "share": round(self.share, 4),
                "display": self.display, "evidence": self.evidence}  # fmt: skip


@dataclass
class Facts:
    """What a review of ``day`` may say, and nothing else."""

    day: str
    zone: str
    start: datetime
    stop: datetime
    segments: list[Record]
    numbers: dict[str, Number]
    categories: list[Share]
    projects: list[Share]
    done: list[Record]
    tomorrow: list[Record]
    evidence: dict[str, Record]  # what the words may cite: the day's records
    background: dict[str, Record]  # cited by code only: deadlines seen on other days
    names: set[str]
    gaps: list[str]
    illustrative: bool
    top: dict[str, list[str]] = field(default_factory=dict)  # unit type -> ids, most attended first
    apps: list[Share] = field(default_factory=list)  # phone apps by time (shares of phone time)
    plan: list[Record] = field(default_factory=list)  # planned blocks against what the day was

    @property
    def active(self) -> bool:
        """Anything at the desk or anywhere for a review to be about."""
        return bool(self.segments)

    def forms(self) -> set[str]:
        """Every number the words may contain: the numbers here, and the date's own."""
        out: set[str] = set()
        for number in self.numbers.values():
            out |= number.forms
        for item in self.categories + self.projects + self.apps:
            out |= duration_forms(item.seconds) | share_forms(item.share)
        for moment in (self.start, self.stop):
            out |= {str(moment.year), str(moment.month), str(moment.day)}
        return out

    @property
    def digest(self) -> str:
        """Hash of the facts: two reviews written from the same facts would say the same."""
        value = {
            "numbers": [n.record() for n in self.numbers.values()],
            "categories": [c.record() for c in self.categories],
            "projects": [p.record() for p in self.projects], "apps": [a.record() for a in self.apps],
            "plan": self.plan,
            "done": self.done, "tomorrow": self.tomorrow,
            "evidence": self.evidence, "background": self.background, "gaps": self.gaps,
            "illustrative": self.illustrative,
        }  # fmt: skip
        return hashlib.blake2b(json.dumps(value, sort_keys=True).encode(), digest_size=16).hexdigest()


def _seconds(segment: Record) -> float:
    return (instant(segment["until"]) - instant(segment["t"])).total_seconds()


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


def _local(value: str, zone: ZoneInfo) -> str:
    return instant(value).astimezone(zone).isoformat()


def _segment_text(segment: Record) -> str:
    where = segment["where"]
    if where == "desk":
        what = segment["category"] + (f", {segment['project']}" if segment["project"] else "")
        return _clip(f"At the desk, {what}: {segment['doing']}")
    if where == "away":
        return _clip(f"Away from the desk: {segment['doing']}")
    if where.startswith("place:"):
        return _clip(f"At {segment['doing']}")
    return _clip(segment["doing"])


def _segment_names(segment: Record) -> set[str]:
    names = {segment["doing"]}
    broad = segment.get("broad") or ""
    if broad.startswith("app:"):
        names.add(app_name(broad.removeprefix("app:")))
    elif broad.startswith("site:"):
        names.add(broad.removeprefix("site:"))
    for value in (segment.get("category"), segment.get("project")):
        if value:
            names.add(value)
    return names


def _units(store: LifeStore, start: datetime, stop: datetime) -> list[Record]:
    """The day's search units of the kinds a review counts, from the vault database, after
    bringing the calendar days the review day spans up to date."""
    days = sorted({start.date().isoformat(), (stop - timedelta(seconds=1)).date().isoformat()})
    known = [d for d in days if store.knows(d)]
    if known:
        store.ensure(known)
    return store.window_units(start.timestamp(), stop.timestamp(), UNIT_TYPES) if known else []


def collect(vault: Vault, store: LifeStore, built: Built) -> Facts:
    """The facts of the review day ``built`` is the timeline of."""
    tz = ZoneInfo(built.zone)
    start, stop = window(built.day, tz)
    segments = built.segments
    evidence: dict[str, Record] = {}
    names: set[str] = set()
    illustrative = False

    def cite(n: int) -> str:
        segment = segments[n]
        key = f"timeline:{n}"
        if key not in evidence:
            evidence[key] = {
                "t": segment["t"], "until": segment["until"], "day": built.day, "kind": "timeline",
                "text": _segment_text(segment), "fidelity": segment["fidelity"], "records": segment["evidence"][:5],
            }  # fmt: skip
        return key

    desk = [n for n, s in enumerate(segments) if s["where"] == "desk"]
    away = [n for n, s in enumerate(segments) if s["where"] == "away"]
    places = [n for n, s in enumerate(segments) if s["where"].startswith("place:")]
    seconds = {n: _seconds(segments[n]) for n in range(len(segments))}
    longest_first = sorted(range(len(segments)), key=lambda n: (-seconds[n], n))
    for n, segment in enumerate(segments):
        illustrative |= segment["fidelity"] == "illustrative"
        if seconds[n] >= 60:
            names |= _segment_names(segment)

    numbers: dict[str, Number] = {}

    def add(key: str, label: str, value: float | None, display: str, ids: list[int | str], forms: set[str]) -> None:
        cited = [cite(i) if isinstance(i, int) else i for i in ids]
        numbers[key] = Number(key, label, value, display, list(dict.fromkeys(cited)), {f for f in forms if f})

    desk_s = sum(seconds[n] for n in desk)
    if desk:
        add("desk", "At the desk", round(desk_s, 1), duration(desk_s), [n for n in longest_first if n in set(desk)][:3],
            duration_forms(desk_s))  # fmt: skip
    away_s = sum(seconds[n] for n in away)
    if away:
        add("away", "Away from the desk", round(away_s, 1), duration(away_s),
            [n for n in longest_first if n in set(away)][:3], duration_forms(away_s))  # fmt: skip
    place_s = sum(seconds[n] for n in places)
    if places:
        add("places", "At places", round(place_s, 1), duration(place_s), [n for n in longest_first if n in set(places)][:3],
            duration_forms(place_s))  # fmt: skip
    if desk:
        first, last = desk[0], desk[-1]
        t0 = instant(segments[first]["t"]).astimezone(tz)
        t1 = instant(segments[last]["until"]).astimezone(tz)
        add("span", "First and last at the desk", None, f"{clock(t0)}–{clock(t1)}", [first, last],
            clock_forms(t0) | clock_forms(t1))  # fmt: skip
        # At the desk when the review day began or ended (04:00): the words say so.
        numbers["span"].parts = {"from_start": t0 == start, "to_end": t1 == stop}
        best = max(desk, key=lambda n: (seconds[n], -n))
        best_t = instant(segments[best]["t"]).astimezone(tz)
        what = segments[best]["category"] + (f", {segments[best]['project']}" if segments[best]["project"] else "")
        add("longest", "Longest stretch on one thing", round(seconds[best], 1),
            f"{duration(seconds[best])} ({what}, from {clock(best_t)})", [best],
            duration_forms(seconds[best]) | clock_forms(best_t))  # fmt: skip
        numbers["longest"].parts = {"category": segments[best]["category"], "project": segments[best]["project"],
                                    "from": clock(best_t)}  # fmt: skip
        focus = [n for n in desk if seconds[n] >= FOCUS_S]
        focus_s = sum(seconds[n] for n in focus)
        add("focus", "Stretches of 25 min or more", len(focus),
            f"{len(focus)}, {duration(focus_s)} in all" if focus else "none",
            sorted(focus, key=lambda n: -seconds[n])[:3], {str(len(focus)), "25"} | duration_forms(focus_s))  # fmt: skip
        switches = sum(segments[n]["switches"] for n in desk)
        rate = switches / (desk_s / 3600) if desk_s >= 600 else 0.0
        most = sorted(desk, key=lambda n: -segments[n]["switches"])[:3]
        add("switches", "Glances at something else (under 20 s)", switches, str(switches), most, {str(switches), "20"})
        if switches:
            add("switch_rate", "Glances an hour at the desk", round(rate, 1), f"{round(rate)} an hour", most,
                {str(round(rate)), normal(f"{rate:.1f}") or "", "20"})  # fmt: skip
        add("changes", "Changes of what you were doing", len(desk), str(len(desk)), [desk[0]], {str(len(desk))})
        midnight = datetime.combine(start.date() + timedelta(days=1), datetime.min.time(), tz).timestamp()
        late = []
        late_s = 0.0
        for n in desk:
            a, b = instant(segments[n]["t"]).timestamp(), instant(segments[n]["until"]).timestamp()
            if b > midnight:
                late.append(n)
                late_s += b - max(a, midnight)
        if late_s >= 60:
            add("late", "At the desk after midnight", round(late_s, 1), duration(late_s), late[-3:],
                duration_forms(late_s) | {"12", "0:00", "12:00"})  # fmt: skip
        other = [n for n in desk if segments[n]["category"] == OTHER]
        other_s = sum(seconds[n] for n in other)
        share = other_s / desk_s if desk_s else 0.0
        add("other", "Desk time in no category", round(share, 4), f"{round(share * 100)}%",
            sorted(other, key=lambda n: -seconds[n])[:3] or [desk[0]], share_forms(share))  # fmt: skip
        hidden = [n for n in desk if segments[n]["category"] == PRIVATE]
        hidden_s = sum(seconds[n] for n in hidden)
        if hidden_s >= 60:
            add("private", "Pages not recorded", round(hidden_s, 1), duration(hidden_s),
                sorted(hidden, key=lambda n: -seconds[n])[:3], duration_forms(hidden_s))  # fmt: skip

    def shares(field_name: str) -> list[Share]:
        total: dict[str, float] = defaultdict(float)
        members: dict[str, list[int]] = defaultdict(list)
        for n in desk:
            value = segments[n][field_name]
            if value:
                total[value] += seconds[n]
                members[value].append(n)
        out = []
        for name in sorted(total, key=lambda k: (-total[k], k)):
            ids = [cite(n) for n in sorted(members[name], key=lambda n: (-seconds[n], n))[:3]]
            out.append(Share(name, total[name], total[name] / desk_s if desk_s else 0.0, ids))
        return out

    categories = shares("category")
    projects = shares("project")

    # Units: reading, searching, copying, conversations; the most attended first for citing.
    units = _units(store, start, stop)
    by_type: dict[str, list[Record]] = defaultdict(list)
    for unit in units:
        by_type[unit["type"]].append(unit)
        illustrative |= unit["fidelity"] == "illustrative"
    top: dict[str, list[str]] = {}
    for kind, group in by_type.items():
        ranked = sorted(group, key=lambda u: (-(u["attention"] or 0.0), u["ts"]))[:CITED_PER_TYPE]
        top[kind] = []
        for unit in sorted(ranked, key=lambda u: u["ts"]):
            evidence[unit["evidence_id"]] = {
                "t": _local(unit["t"], tz), "until": _local(unit["t_end"], tz) if unit["t_end"] else None,
                "day": unit["day"], "kind": unit["type"], "text": _clip(unit["text"]), "fidelity": unit["fidelity"],
            }  # fmt: skip
            top[kind].append(unit["evidence_id"])
    reads = by_type.get("web.read", [])
    pages: dict[str, list[Record]] = defaultdict(list)
    for unit in reads:
        page = unit["text"].removeprefix("Read in ").split(": ", 1)[0]
        pages[re.sub(r"^(.+) \(\1\)$", r"\1", page)].append(unit)  # "Claude (Claude)": an app's own window
    if reads:
        add("reads", "Passages read line by line", len(reads), f"{len(reads)} on {len(pages)} page{'s' * (len(pages) != 1)}",
            top["web.read"][:3], {str(len(reads)), str(len(pages))})  # fmt: skip
        numbers["reads"].parts = {"pages": len(pages)}
    for kind, key, label in (("web.search", "searches", "Searches"), ("web.selection", "copies", "Copied or selected")):
        group = by_type.get(kind, [])
        if group:
            add(key, label, len(group), str(len(group)), top[kind][:3], {str(len(group))})
    talks = [s for s in segments if s["talk"]]
    moments = list(dict.fromkeys(m for s in talks for m in s["talk"]))
    if moments:
        audio = {u["moment_id"]: u for u in by_type.get("moment.audio", []) + by_type.get("moment.video", [])}
        talk_s = sum((instant(u["t_end"]) - instant(u["t"])).total_seconds() for m, u in audio.items()
                     if m in moments and u["t_end"])  # fmt: skip
        ids = [audio[m]["evidence_id"] for m in moments if m in audio] or [
            cite(n) for n, s in enumerate(segments) if s["talk"]
        ][:3]
        add("talk", "Conversations recorded", len(moments),
            f"{len(moments)}" + (f", {duration(talk_s)}" if talk_s else ""), ids[:3],
            {str(len(moments))} | (duration_forms(talk_s) if talk_s else set()))  # fmt: skip

    # Beside the timeline: typing and mouse counts, lines noted, phone apps, commits.
    calendar = sorted({start.date().isoformat(), (stop - timedelta(seconds=1)).date().isoformat()})

    def day_of(t: float) -> str:
        return datetime.fromtimestamp(t, tz).date().isoformat()

    desk_spans = [(instant(segments[n]["t"]).timestamp(), instant(segments[n]["until"]).timestamp(), n) for n in desk]
    buckets = signals.inputs(vault, calendar, start, stop)
    if buckets:
        active: dict[int, float] = defaultdict(float)
        for bucket in buckets:
            for a, b, n in desk_spans:
                if a < bucket.t1 and bucket.t0 < b:
                    active[n] += signals.overlap(bucket.t0, bucket.t1, a, b)
        active_s = sum(active.values())
        busiest = sorted(active, key=lambda n: (-active[n], n))[:3] or longest_first[:1]
        keys, clicks = sum(b.keys for b in buckets), sum(b.clicks for b in buckets)
        if desk:
            share = active_s / desk_s if desk_s else 0.0
            add("active", "Desk time with typing or the mouse", round(active_s, 1),
                f"{duration(active_s)} ({round(share * 100)}%)", busiest, duration_forms(active_s) | share_forms(share))  # fmt: skip
            numbers["active"].parts = {"share": share}
        if busiest:
            add("keys", "Keys pressed", keys, f"{keys:,}", busiest, {str(keys)})
            add("clicks", "Mouse clicks", clicks, f"{clicks:,}", busiest, {str(clicks)})
    notes = signals.doing(vault, calendar, start, stop)
    if notes:
        ids = []
        times: set[str] = set()
        for note in notes:
            key = f"capture:doing:{note['id']}"
            evidence[key] = {"t": _local(note["t"], tz), "until": None, "day": day_of(note["ts"]), "kind": "doing",
                             "text": _clip(f"Noted: {note['text']}"), "fidelity": "known"}  # fmt: skip
            ids.append(key)
            names.add(note["text"])
            times |= clock_forms(datetime.fromtimestamp(note["ts"], tz))
        top["doing"] = ids[-CITED_PER_TYPE:]
        add("noted", "Lines you noted (Alt+Shift+N)", len(notes), str(len(notes)), ids[-3:], {str(len(notes))} | times)
        numbers["noted"].parts = {"notes": [(clock(datetime.fromtimestamp(x["ts"], tz)), x["text"], k)
                                            for x, k in zip(notes, ids, strict=True)]}  # fmt: skip
    sessions = signals.phone(vault, calendar, start, stop)
    apps: list[Share] = []
    if sessions:
        keyed = []
        for s in sessions:
            key = f"phone:{s.ids[0]}" if s.ids else f"phone:{s.app}:{int(s.t0)}"
            evidence[key] = {"t": _iso(s.t0, tz), "until": _iso(s.t1, tz), "day": day_of(s.t0), "kind": "phone.app",
                             "text": _clip(f"{s.app} on the phone, {duration(s.t1 - s.t0)}"),
                             "fidelity": "known" if s.closed else "estimated"}  # fmt: skip
            keyed.append((key, s))
            names.add(s.app)
        phone_s = sum(s.t1 - s.t0 for s in sessions)
        longest = [k for k, s in sorted(keyed, key=lambda ks: -(ks[1].t1 - ks[1].t0))]
        top["phone.app"] = sorted(longest[:CITED_PER_TYPE], key=lambda k: evidence[k]["t"])
        add("phone", "Phone time in the apps you log", round(phone_s, 1), duration(phone_s), longest[:3],
            duration_forms(phone_s))  # fmt: skip
        picked = signals.pickups(sessions)
        first = {id(s): k for k, s in keyed}
        add("pickups", "Phone pickups", len(picked), str(len(picked)), [first[id(s)] for s in picked][:3],
            {str(len(picked))})  # fmt: skip
        on_desk: dict[str, float] = defaultdict(float)
        for key, s in keyed:
            for a, b, _n in desk_spans:
                on_desk[key] += signals.overlap(s.t0, s.t1, a, b)
        desk_phone_s = sum(on_desk.values())
        if desk_phone_s >= 60:
            ids = sorted((k for k in on_desk if on_desk[k] > 0), key=lambda k: -on_desk[k])[:3]
            add("phone_desk", "Phone during desk time", round(desk_phone_s, 1), duration(desk_phone_s), ids,
                duration_forms(desk_phone_s))  # fmt: skip
        by_app: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for key, s in keyed:
            by_app[s.app].append((key, s.t1 - s.t0))
        for app, items in sorted(by_app.items(), key=lambda kv: (-sum(x for _, x in kv[1]), kv[0]))[:APPS_SHOWN]:
            seconds_app = sum(x for _, x in items)
            ids = [k for k, _ in sorted(items, key=lambda kx: -kx[1])[:3]]
            apps.append(Share(app, seconds_app, seconds_app / phone_s if phone_s else 0.0, ids))
    made, git_errors = git.commits(vault, start, stop) if _reads_git(vault) else ([], [])
    commit_ids: dict[str, list[str]] = defaultdict(list)  # project -> your commits
    others: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))  # project -> author -> ids
    for commit in made:
        by = "" if commit.mine else f" (by {commit.author or 'someone else'})"
        evidence[commit.id] = {"t": _local(commit.t, tz), "until": None, "day": instant(commit.t).astimezone(tz).date().isoformat(),
                               "kind": "commit", "text": _clip(f"Commit in {commit.project}{by}: {commit.subject}"),
                               "fidelity": "known"}  # fmt: skip
        if commit.mine:
            commit_ids[commit.project].append(commit.id)
        else:
            others[commit.project][commit.author or "someone else"].append(commit.id)
            names.add(commit.author)
        names.add(commit.project)
    mine_ids = [c.id for c in made if c.mine]
    other_ids = [c.id for c in made if not c.mine]
    if made:
        top["commit"] = [c.id for c in made][-CITED_PER_TYPE:]
    if mine_ids:
        add("commits", "Commits", len(mine_ids), str(len(mine_ids)), mine_ids[-3:], {str(len(mine_ids))})
        numbers["commits"].parts = {"projects": {p: len(v) for p, v in commit_ids.items()}}
    if other_ids:
        by_author: dict[str, int] = defaultdict(int)
        for project in others.values():
            for author, ids in project.items():
                by_author[author] += len(ids)
        add("commits_others", "Commits by others in your projects", len(other_ids), str(len(other_ids)),
            other_ids[-3:], {str(len(other_ids))} | {str(n) for n in by_author.values()})  # fmt: skip
        numbers["commits_others"].parts = {"authors": dict(by_author)}

    site_rules, site_errors = sites.rules(vault)
    hits = sites.finished(vault, calendar, start, stop, site_rules)
    by_site: dict[tuple[str, str], list[sites.Hit]] = defaultdict(list)
    for hit in hits:
        evidence[hit.id] = {"t": _local(hit.t, tz), "until": None, "day": instant(hit.t).astimezone(tz).date().isoformat(),
                            "kind": "site", "text": _clip(f"{hit.rule.name}, {hit.rule.label}: {hit.item}"),
                            "fidelity": "known"}  # fmt: skip
        by_site[(hit.rule.name, hit.rule.label)].append(hit)
        names.add(hit.rule.name)
    if hits:
        top["site"] = [h.id for h in hits][-CITED_PER_TYPE:]
        add("sites", "Finished on websites", len(hits), str(len(hits)), [h.id for h in hits][-3:],
            {str(len(hits))} | {str(len(g)) for g in by_site.values()})  # fmt: skip
        numbers["sites"].parts = {"sites": [(name, label, len(g), [h.id for h in g][-3:]) for (name, label), g in by_site.items()]}

    # What got done: time per project, commits, things finished on websites, and pages read with care.
    done: list[Record] = []
    for project in projects:
        if project.seconds < DONE_MIN_S:
            continue
        kinds: dict[str, float] = defaultdict(float)
        for n in desk:
            if segments[n]["project"] == project.name:
                kinds[segments[n]["category"]] += seconds[n]
        parts = ", ".join(f"{k} {duration(v)}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:3])
        done.append({"text": f"{project.name}: {duration(project.seconds)} ({parts})", "kind": "project",
                     "evidence": project.evidence})  # fmt: skip
    for (name, label), group in by_site.items():
        items = "; ".join(h.item for h in group[-3:])
        done.append({"text": f"{name}: {len(group)} {label} ({_clip(items)})", "kind": "site",
                     "evidence": [h.id for h in group][-3:]})  # fmt: skip
    for project in list(dict.fromkeys([*commit_ids, *others])):
        ids = commit_ids.get(project, [])
        theirs = others.get(project, {})
        parts = []
        if ids:
            subjects = "; ".join(evidence[i]["text"].split(": ", 1)[1] for i in ids[-3:])
            parts.append(f"{len(ids)} commit{'s' * (len(ids) != 1)} by you ({_clip(subjects)})")
        parts += [f"{len(v)} by {author}" for author, v in sorted(theirs.items(), key=lambda kv: -len(kv[1]))]
        cited = (ids[-3:] + [i for v in theirs.values() for i in v[-1:]])[:4]
        done.append({"text": f"{project}: " + "; ".join(parts), "kind": "commits", "evidence": cited})
    for page, group in sorted(pages.items(), key=lambda kv: -len(kv[1])):
        if len(group) >= READ_PAGES_MIN:
            ids = [u["evidence_id"] for u in group if u["evidence_id"] in evidence][:3]
            if ids:
                done.append({"text": f"Read {len(group)} passages in {page}", "kind": "read", "evidence": ids})
        if len(done) >= 8:
            break

    # Tomorrow: deadlines in the week after the day, and where the desk work stopped.
    tomorrow: list[Record] = []
    background: dict[str, Record] = {}
    first = (start.date() + timedelta(days=1)).isoformat()
    last = (start.date() + timedelta(days=DEADLINE_DAYS)).isoformat()
    if store.path.exists():
        seen: set[str] = set()
        for row in store.deadlines(first, last, stop.date().isoformat()):
            if row["key"] in seen or not row["t"] or instant(row["t"]) >= stop:
                continue
            seen.add(row["key"])
            due = row["key"].split("|")[0]
            target = evidence if row["day"] in (start.date().isoformat(), stop.date().isoformat()) else background
            target.setdefault(row["evidence_id"], {
                "t": _local(row["t"], tz), "until": None, "day": row["day"], "kind": row["type"] or "deadline",
                "text": _clip(row["text"] or row["name"]), "fidelity": row["fidelity"] or "known",
            })  # fmt: skip
            days_left = (date.fromisoformat(due[:10]) - start.date()).days
            when = "tomorrow" if days_left == 1 else f"in {days_left} days"
            tomorrow.append({"text": f"{row['name']} ({when})", "kind": "deadline", "due": due,
                             "evidence": [row["evidence_id"]]})  # fmt: skip
            names.add(row["name"])
    for n in longest_first[:SEGMENTS_CITED]:  # what a model may cite besides what the numbers do
        if seconds[n] >= 120:
            cite(n)
    lasting = [n for n in desk if seconds[n] >= 5 * 60]
    if lasting:
        n = lasting[-1]
        segment = segments[n]
        at = instant(segment["until"]).astimezone(tz)
        what = f" on {segment['project']}" if segment["project"] else f", {segment['doing']}"
        tomorrow.append({"text": f"Where the desk work stopped, at {clock(at)}: {segment['category']}{what}",
                         "kind": "resume", "due": None, "evidence": [cite(n)]})  # fmt: skip

    # Planned against actual: the daily note's Plan section and the calendar files.
    blocks, plan_errors = plan.planned(vault, _daily_note(store, built.day), built.day, start, stop)
    planned: list[Record] = []
    plan_forms: set[str] = set()
    outcomes = plan.compare(blocks, segments)
    for k, outcome in enumerate(outcomes):
        block = outcome.block
        t0, t1 = datetime.fromtimestamp(block.t0, tz), datetime.fromtimestamp(block.t1, tz)
        key = f"plan:{k}"
        evidence[key] = {"t": t0.isoformat(timespec="seconds"), "until": t1.isoformat(timespec="seconds"),
                         "day": t0.date().isoformat(), "kind": "plan",
                         "text": _clip(f"Planned {clock(t0)}–{clock(t1)}: {block.text}"), "fidelity": "known"}  # fmt: skip
        names.add(block.text)
        plan_forms |= clock_forms(t0) | clock_forms(t1)
        said = {"done": "went as planned", "partly": f"partly ({round(outcome.share * 100)}%)", "missed": "missed",
                "elsewhere": f"went to {outcome.instead} instead ({duration(outcome.instead_s)})"}[outcome.status]  # fmt: skip
        plan_forms |= share_forms(outcome.share)
        if outcome.instead:
            plan_forms |= duration_forms(outcome.instead_s)
            names.add(outcome.instead)
        planned.append({"text": f"{clock(t0)}–{clock(t1)} {block.text}: {said}", "status": outcome.status,
                        "from": t0.isoformat(timespec="seconds"), "to": t1.isoformat(timespec="seconds"),
                        "source": block.source, "evidence": [key] + [cite(n) for n in outcome.segments[:2]]})  # fmt: skip
    if planned:
        kept = sum(p["status"] == "done" for p in planned)
        off = sum(p["status"] in ("elsewhere", "missed") for p in planned)
        add("plan", "Planned blocks that went as planned", kept, f"{kept} of {len(planned)}",
            [f"plan:{k}" for k in range(len(planned))][:3], {str(kept), str(len(planned)), str(off)} | plan_forms)  # fmt: skip
        numbers["plan"].parts = {"items": planned, "off": off}

    # When the long stretches of the last two weeks happened: tomorrow's best hours.
    hours = plan.best_hours(vault, built.day, tz)
    if hours:
        target = next((t["text"].rsplit(" (", 1)[0] for t in tomorrow if t["kind"] == "deadline"), None)
        target = target or (projects[0].name if projects else "the hardest task of the day")
        ids = []
        for name, n, segment in hours["examples"]:
            if name == built.day and n < len(segments):
                ids.append(cite(n))
                continue
            key = f"timeline:{name}:{n}"
            background[key] = {"t": segment["t"], "until": segment["until"], "day": name, "kind": "timeline",
                               "text": _segment_text(segment), "fidelity": segment.get("fidelity", "known")}  # fmt: skip
            ids.append(key)
        if ids:
            tomorrow.append({"text": f"Your stretches of 25 min or more in the last {hours['days']} days ran most between "
                                     f"{hours['from']} and {hours['to']}: keep that time for {target}",
                             "kind": "hours", "due": None, "evidence": ids})  # fmt: skip

    for share in categories + projects:
        names.add(share.name)
    gaps = _gaps(vault, built, segments, desk_s, inputs=bool(buckets), phone=bool(sessions),
                 git_errors=git_errors + plan_errors + site_errors)  # fmt: skip
    return Facts(built.day, built.zone, start, stop, segments, numbers, categories, projects, done, tomorrow,
                 evidence, background, names, gaps, illustrative, top, apps, planned)  # fmt: skip


def _daily_note(store: LifeStore, day: str) -> Path | None:
    """The daily note of ``day`` (where its Plan section is), wherever it was moved or renamed."""
    from ..notes import Notes

    try:
        return Notes(store).path_for("day", day, "Daily", day)
    except (OSError, ValueError):
        return None


def _reads_git(vault: Vault) -> bool:
    """Commits are counted unless the environment sets DAYCUBE_GIT=off."""
    import os

    return os.environ.get("DAYCUBE_GIT", "on").strip().lower() not in ("off", "0", "false", "no")


def _iso(t: float, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(t, zone).isoformat(timespec="seconds")


def _gaps(
    vault: Vault, built: Built, segments: list[Record], desk_s: float, *, inputs: bool = False, phone: bool = False,
    git_errors: list[str] | None = None,
) -> list[str]:  # fmt: skip
    """What the review cannot see, said once in plain words."""
    gaps = []
    day = vault.day_path(built.day, must_exist=False)
    if not (day / "day.json").is_file():
        gaps.append("No phone records for this day: places and conversations away from the PC are not in it.")
    if not any(s["where"] == "desk" for s in segments):
        gaps.append("Nothing at the desk was recorded.")
    missing = []
    if not inputs:
        missing.append("typing and mouse use")
    if not phone:
        missing.append("phone apps (iOS Shortcuts, docs/phone.md)")
    if missing:
        gaps.append(f"Not recorded for this day: {' and '.join(missing)}.")
    for error in git_errors or []:
        gaps.append(error if "left out" in error else f"Commits: {error}")
    other = sum(_seconds(s) for s in segments if s["where"] == "desk" and s["category"] == OTHER)
    if desk_s and other / desk_s > 0.1:
        gaps.append(f"{duration(other)} at the desk is in no category: rules in vault/categories.yaml would name it.")
    # Files that could not be read stay until they are fixed; a failed call to a model passes, and
    # is in the timeline's report (doctor) rather than in the facts, which it would keep changing.
    for error in built.report.get("errors", []):
        if "left out" in error:
            gaps.append(f"Left out: {error}")
    return gaps

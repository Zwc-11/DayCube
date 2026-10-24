"""Entities and links from a day's units: the knowledge graph, without a language model.

Every entity is something the records name exactly, so the graph needs no guessing:

| Kind | Key | From |
| --- | --- | --- |
| ``site`` | domain without ``www.`` | every capture record with a URL |
| ``page`` | URL without fragment and tracking parameters | page visits, reads, dwells, looks, clicks, copies, media |
| ``query`` | the search, lower case, spaces collapsed | searches |
| ``course`` | a course code such as ``CS 136`` | page titles and the text read or stared at |
| ``deadline`` | ISO date (and time) plus the course | "due ..." phrases in text read or stared at |
| ``place`` | place id, named as in the day (or as renamed) | vault day visits (its events name it in their own ``place`` column) |
| ``person``, ``topic`` | the event's own names | vault day events' ``people`` and ``topics`` |

Links say how two entities met, each with the record that shows it: a page is ``part_of`` its site,
a search ``led_to`` the page opened from its results, a page ``led_to`` the page a link on it opened,
a page ``mentions`` a course, a course is ``due`` at a deadline, a place ``led_to`` the next place
visited that day.
Each mention ties a unit to an entity: ``on`` (it happened on that page or site), ``about`` (it
names it), ``at`` (a place), ``with`` (a person).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..capture.synthesize import CaptureDay, _domain
from ..time import instant
from ..vault import Record

Entity = tuple[str, str, str]  # (kind, key, name)

TRACKING = re.compile(r"^(utm_.+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src|si)$")
SEARCH_HOSTS = ("google.", "bing.com", "duckduckgo.com", "search.yahoo.", "ecosia.org", "search.brave.com")
# Two to five capital letters, an optional space, three digits and an optional letter: CS 136,
# MATH135, AFM 101, STAT 230, CO 250, PHYS 121L. Codes such as "PM 11" (two digits) do not match.
COURSE = re.compile(r"\b([A-Z]{2,5}) ?(\d{3}[A-Z]?)\b")
NOT_COURSES = frozenset({"PDF", "ISO", "UTC", "EST", "EDT", "GMT", "PST", "PDT", "HTTP", "HTML", "COVID", "RFC"})
MONTHS = {m: i + 1 for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"))}  # fmt: skip
MONTH_ABBREVIATIONS = {name[:3]: number for name, number in MONTHS.items()}
DUE = re.compile(
    r"\bdue\b[^.;:\n]{0,40}?\b(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.? (?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,? (?P<year>\d{4}))?(?:[^.;\n]{0,12}?\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))? ?(?P<ampm>[ap]\.?m\.?)?)?",
    re.IGNORECASE,
)


def canonical_url(url: str) -> str:
    """The URL a page is known by: no fragment, no tracking parameters, no trailing slash on the path."""
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), (parts.hostname or "").lower(), path, query, ""))


def courses(text: str) -> list[str]:
    found = []
    for letters, digits in COURSE.findall(text or ""):
        if letters not in NOT_COURSES:
            found.append(f"{letters} {digits}")
    return list(dict.fromkeys(found))


def deadlines(text: str, seen_on: date) -> list[str]:
    """ISO dates (with times when given) of "due <month> <day> [at <time>]" phrases; without a year,
    the next such date on or after the day it was seen."""
    out = []
    for match in DUE.finditer(text or ""):
        month = MONTHS.get(match["month"].lower()) or MONTH_ABBREVIATIONS[match["month"].lower()[:3]]
        day = int(match["day"])
        year = int(match["year"]) if match["year"] else seen_on.year
        try:
            when = date(year, month, day)
        except ValueError:
            continue
        if not match["year"] and when < seen_on:
            when = date(year + 1, month, day)
        if match["hour"] and (match["ampm"] or match["minute"]):
            hour, minute = int(match["hour"]), int(match["minute"] or 0)
            if match["ampm"]:
                hour = hour % 12 + (12 if match["ampm"].lower().startswith("p") else 0)
            if hour < 24 and minute < 60:
                out.append(datetime(when.year, when.month, when.day, hour, minute).isoformat(timespec="minutes"))
                continue
        out.append(when.isoformat())
    return list(dict.fromkeys(out))


@dataclass
class Graph:
    """A day's mentions (evidence id, entity, role) and links (source, target, kind, evidence id)."""

    mentions: list[tuple[str, Entity, str]] = field(default_factory=list)
    links: list[tuple[Entity, Entity, str, str | None]] = field(default_factory=list)

    def mention(self, evidence: str, entity: Entity, role: str) -> None:
        self.mentions.append((evidence, entity, role))

    def link(self, source: Entity, target: Entity, kind: str, evidence: str | None) -> None:
        if source != target:
            self.links.append((source, target, kind, evidence))


def readable(when: str) -> str:
    """``2026-10-30T23:59`` as ``Fri Oct 30, 11:59 PM`` (no platform-specific strftime flags)."""
    moment = datetime.fromisoformat(when)
    text = f"{moment:%a} {moment:%b} {moment.day}"
    if "T" in when:
        text += f", {(moment.hour % 12) or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
    return text


def page(url: str, title: str | None) -> Entity:
    return ("page", canonical_url(url), (title or "").strip() or canonical_url(url))


def site(url: str) -> Entity:
    domain = _domain(url)
    return ("site", domain, domain)


def is_search(url: str | None) -> bool:
    host = _domain(url)
    return any(host.startswith(s) or host.endswith(s) or s in host for s in SEARCH_HOSTS)


def extract(
    day: str,
    units: list[Record],
    capture: CaptureDay | None,
    events: list[Record] | None = None,
    places: dict[str, str] | None = None,
) -> Graph:
    """``places``: place id -> name, from the day's ``day.json``."""
    graph = Graph()
    seen_on = date.fromisoformat(day)
    by_evidence = {u["evidence_id"]: u for u in units}
    if capture is not None:
        _capture(graph, capture, by_evidence, seen_on)
    # Places are mentioned by their visits only: every event already carries its place in the
    # units table, and a mention per event would cost a row per record for nothing new.
    for event in events or []:
        evidence = event["id"]
        if evidence not in by_evidence or not (event.get("people") or event.get("topics")):
            continue
        for person in event.get("people") or []:
            graph.mention(evidence, ("person", str(person).casefold(), str(person)), "with")
        for topic in event.get("topics") or []:
            graph.mention(evidence, ("topic", str(topic).casefold(), str(topic)), "about")
    previous: Entity | None = None
    visits = [u for u in units if u["type"] == "place.visit" and u.get("place")]
    for unit in sorted(visits, key=lambda u: instant(u["t"])):
        name = (places or {}).get(unit["place"]) or unit["text"].removeprefix("Visited ")
        place = ("place", unit["place"], name)
        graph.mention(unit["evidence_id"], place, "at")
        if previous is not None:
            graph.link(previous, place, "led_to", unit["evidence_id"])
        previous = place
    return graph


def _on(graph: Graph, evidence: str, url: str | None, title: str | None) -> Entity | None:
    if not url or not urlsplit(url).hostname:
        return None
    p, s = page(url, title), site(url)
    graph.mention(evidence, p, "on")
    graph.mention(evidence, s, "on")
    graph.link(p, s, "part_of", evidence)
    for code in courses(title or ""):
        course = ("course", code, code)
        graph.mention(evidence, course, "about")
        graph.link(p, course, "mentions", evidence)
    return p


def _text(graph: Graph, evidence: str, where: Entity | None, title: str, text: str, seen_on: date) -> None:
    codes = courses(text) or courses(title)
    for code in courses(text):
        course = ("course", code, code)
        graph.mention(evidence, course, "about")
        if where is not None:
            graph.link(where, course, "mentions", evidence)
    for when in deadlines(text, seen_on):
        label = f"{codes[0]} due {readable(when)}" if codes else f"Due {readable(when)}"
        deadline = ("deadline", f"{when}|{codes[0] if codes else ''}", label)
        graph.mention(evidence, deadline, "about")
        if codes:
            graph.link(("course", codes[0], codes[0]), deadline, "due", evidence)
        elif where is not None:
            graph.link(where, deadline, "due", evidence)


def _capture(graph: Graph, c: CaptureDay, units: dict[str, Record], seen_on: date) -> None:
    for v in c.visits:
        _on(graph, f"capture:visit:{v.first_id}", v.url, v.title)
    for r in c.reads:
        evidence = f"capture:read:{r['id']}"
        where = _on(graph, evidence, r.get("url"), r.get("title"))
        _text(
            graph,
            evidence,
            where,
            r.get("title") or "",
            " ".join(x.get("text", "") for x in r.get("lines", [])),
            seen_on,
        )
    for d in c.dwells:
        evidence = f"capture:dwell:{d.get('dwell') or d['id']}"
        where = _on(graph, evidence, d.get("url"), d.get("title"))
        text = d.get("text") or c.texts.get((d.get("doc"), d.get("block")), ("", ""))[1]
        _text(graph, evidence, where, d.get("title") or "", text, seen_on)
    for look in c.looks:
        evidence = f"capture:look:{look['doc']}:{look['block']}"
        where = _on(graph, evidence, look.get("url"), look.get("title"))
        _text(graph, evidence, where, look.get("title") or "", look["text"], seen_on)
    for k in c.clicks:
        evidence = f"capture:click:{k['id']}"
        _on(graph, evidence, k.get("url"), k.get("title"))
    for s in c.selections:
        _on(graph, f"capture:selection:{s['id']}", s.get("url"), s.get("title"))
    for m in c.media:
        _on(graph, f"capture:media:{m['id']}", m.get("url"), m.get("title"))
    # Searches, and the page each led to: the next page opened in that tab from somewhere other
    # than a results page, within five minutes.
    navigations = sorted(c.pages, key=lambda p: instant(p["t"]))
    for s in c.searches:
        evidence = f"capture:search:{s['id']}"
        if evidence not in units:
            continue
        query = ("query", " ".join(s["query"].casefold().split()), s["query"].strip())
        graph.mention(evidence, query, "about")
        start = instant(s["t"])
        for nav in navigations:
            if nav.get("tab") != s.get("tab") or instant(nav["t"]) <= start:
                continue
            if (instant(nav["t"]) - start).total_seconds() > 300:
                break
            if not is_search(nav.get("url")):
                graph.link(query, page(nav["url"], nav.get("title")), "led_to", evidence)
                break
    # A page reached by a link from another page in the same tab.
    last: dict[int, Record] = {}
    for nav in navigations:
        tab = nav.get("tab")
        before = last.get(tab)
        if before is not None and nav.get("transition") == "link" and not is_search(before.get("url")):
            source, target = page(before["url"], before.get("title")), page(nav["url"], nav.get("title"))
            graph.link(source, target, "led_to", f"capture:page:{nav['id']}")
        last[tab] = nav

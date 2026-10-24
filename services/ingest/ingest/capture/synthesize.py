"""From the capture logs of one day to activities and search units.

The receiver writes raw records (``services/capture-win``): browser records (``browser.jsonl``),
the text on screen (``blocks.jsonl``), fixations placed on blocks and lines (``gaze.jsonl``),
dwells (``attention.jsonl``) and line-by-line reads (``reading.jsonl``). This module reads them
once and derives:

- page visits: which page was in front, from when to when, from navigation, focus and idle
  records (a tab in front while the session is idle or locked does not count);
- reads: the lines of a block the eyes went along, from the line decoder;
- dwells: runs on one block longer than reading it would take;
- looks: blocks that held the eyes for a while without being read line by line (a figure, a
  table, a heading), from the fixations' blocks;
- searches, clicks on links and buttons, selections and copies, media played.

Each becomes a unit in the vault database (``ingest.store``), so Ask finds "what did I read about
X yesterday" the way it finds a transcript line. Block texts are sent once per page load, so the
previous day's text snapshots are read too (a page opened before midnight and read after).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from ..time import instant
from ..vault import Record, Vault, file_stamp, read_text, stored

LOGS = ("browser.jsonl", "blocks.jsonl", "gaze.jsonl", "attention.jsonl", "reading.jsonl")
MIN_VISIT_S = 3.0  # shorter spans in front are tab flipping, not visits
MERGE_GAP_S = 60.0  # the same page back in front within this long continues the visit
LOOK_S = 3.0  # gaze on a block this long (without a line-by-line read) is a look worth keeping
MEDIA_S = 30.0
TEXT_LIMIT = 1200  # characters of a unit's text


def _records(path: Path) -> list[Record]:
    """JSON lines, skipping any that do not parse (a torn append, a hand edit): one bad line must
    not hide a day of capture."""
    if not stored(path).exists():
        return []
    out = []
    for line in read_text(path).split("\n"):
        if line.strip():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                out.append(value)
    return out


def capture_days(vault: Vault) -> list[str]:
    """Dates with capture logs, oldest first."""
    folder = vault.root / "capture"
    if not folder.is_dir():
        return []
    days = []
    for path in folder.iterdir():
        if path.is_dir() and len(path.name) == 10 and any(stored(path / name).exists() for name in LOGS):
            try:
                date.fromisoformat(path.name)
            except ValueError:
                continue
            days.append(path.name)
    return sorted(days)


def _sources(vault: Vault, day: str) -> list[Path]:
    folder = vault.root / "capture"
    previous = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    return [folder / day / name for name in LOGS] + [folder / previous / "blocks.jsonl"]


def capture_stamp(vault: Vault, day: str) -> dict[str, list[int]]:
    """Mtime, size and inode of every log a day's capture units come from (compacted or not)."""
    stamp = {}
    for path in _sources(vault, day):
        source = stored(path)
        found = file_stamp(source)
        if found is not None:
            stamp["capture/" + source.relative_to(vault.root / "capture").as_posix()] = list(found)
    return stamp


def _domain(url: str | None) -> str:
    host = urlsplit(url or "").hostname or ""
    return host.removeprefix("www.")


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


@dataclass
class Visit:
    url: str
    title: str
    t0: str
    t1: str
    seconds: float
    first_id: str
    tab: int | None = None


@dataclass
class CaptureDay:
    day: str
    visits: list[Visit] = field(default_factory=list)
    reads: list[Record] = field(default_factory=list)
    dwells: list[Record] = field(default_factory=list)
    looks: list[Record] = field(default_factory=list)
    searches: list[Record] = field(default_factory=list)
    clicks: list[Record] = field(default_factory=list)
    selections: list[Record] = field(default_factory=list)
    media: list[Record] = field(default_factory=list)
    pages: list[Record] = field(default_factory=list)  # navigations, with how each was reached
    texts: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)  # (doc, block) -> (tag, text)
    docs: dict[str, tuple[str, str]] = field(default_factory=dict)  # doc -> (url, title)


def read_capture(vault: Vault, day: str) -> CaptureDay:
    folder = vault.root / "capture" / day
    out = CaptureDay(day)
    previous = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    for path in (vault.root / "capture" / previous / "blocks.jsonl", folder / "blocks.jsonl"):
        for record in _records(path):
            doc = record.get("doc")
            if not doc:
                continue
            out.docs[doc] = (record.get("url", ""), record.get("title", "") or out.docs.get(doc, ("", ""))[1])
            for block in record.get("blocks", []):
                if block.get("text"):
                    out.texts[(doc, block["id"])] = (block.get("tag", ""), block["text"])
    browser = sorted(_records(folder / "browser.jsonl"), key=lambda r: instant(r["t"]))
    out.visits = _visits(browser)
    for record in browser:
        kind = record.get("kind")
        if kind == "search" and record.get("query"):
            out.searches.append(record)
        elif kind == "click" and (record.get("text") or record.get("href")):
            out.clicks.append(record)
        elif kind == "selection" and record.get("text"):
            out.selections.append(record)
        elif kind == "page":
            out.pages.append(record)
    out.media = _media(browser)
    out.reads = [r for r in _records(folder / "reading.jsonl") if r.get("kind") == "read"]
    ends = {}
    for record in _records(folder / "attention.jsonl"):
        # One per dwell: its end when it ended today, else its start.
        if record.get("kind") == "dwell" and (record.get("phase") == "end" or record.get("dwell") not in ends):
            ends[record.get("dwell")] = record
    out.dwells = sorted(ends.values(), key=lambda r: instant(r["t"]))
    out.looks = _looks(_records(folder / "gaze.jsonl"), out)
    return out


def _visits(browser: list[Record]) -> list[Visit]:
    """Spans of the page in front, merged when the same page comes back within ``MERGE_GAP_S``."""
    spans: list[Visit] = []
    current: Visit | None = None
    tabs: dict[int, tuple[str, str]] = {}  # tab -> (url, title) last navigated
    front: int | None = None
    active = True

    def close(t: str) -> None:
        nonlocal current
        if current is not None:
            seconds = (instant(t) - instant(current.t0)).total_seconds()
            if seconds > 0:
                current.t1, current.seconds = t, seconds
                spans.append(current)
        current = None

    def open_(t: str, record_id: str) -> None:
        nonlocal current
        if active and front is not None and front in tabs and tabs[front][0]:
            url, title = tabs[front]
            current = Visit(url, title, t, t, 0.0, record_id, front)

    for record in browser:
        kind, t = record.get("kind"), record["t"]
        if kind == "focus":
            close(t)
            front = record.get("tab")
            if front is not None and record.get("url"):
                tabs[front] = (record["url"], record.get("title") or "")
            elif front is not None:
                tabs.pop(front, None)  # an excluded page in front
            open_(t, record["id"])
        elif kind in ("page", "title") and record.get("tab") is not None:
            tab = record["tab"]
            old = tabs.get(tab)
            tabs[tab] = (record.get("url", ""), record.get("title") or "")
            if tab == front:
                if kind == "page" or old is None or old[0] != tabs[tab][0]:
                    close(t)
                    open_(t, record["id"])
                elif current is not None:
                    current.title = tabs[tab][1] or current.title
        elif kind == "idle":
            if record.get("state") == "active":
                if not active:
                    active = True
                    open_(t, record["id"])
            else:
                close(t)
                active = False
    if current is not None and browser:
        close(browser[-1]["t"])
    merged: list[Visit] = []
    for span in spans:
        last = merged[-1] if merged else None
        if last and last.url == span.url and (instant(span.t0) - instant(last.t1)).total_seconds() <= MERGE_GAP_S:
            last.t1 = span.t1
            last.seconds += span.seconds
            last.title = span.title or last.title
        else:
            merged.append(span)
    return [v for v in merged if v.seconds >= MIN_VISIT_S]


def _media(browser: list[Record]) -> list[Record]:
    playing: dict[int, Record] = {}
    out = []
    for record in browser:
        if record.get("kind") != "media":
            continue
        tab = record.get("tab")
        if record.get("state") == "playing":
            playing.setdefault(tab, record)
        elif tab in playing:
            start = playing.pop(tab)
            seconds = (instant(record["t"]) - instant(start["t"])).total_seconds()
            if seconds >= MEDIA_S:
                out.append({**start, "t_end": record["t"], "seconds": seconds})
    return out


def _looks(fixations: list[Record], day: CaptureDay) -> list[Record]:
    """Blocks that held the eyes ``LOOK_S`` or longer in all, other than those read line by line."""
    read = {(r.get("doc"), r.get("block")) for r in day.reads}
    totals: dict[tuple[str, str], list] = defaultdict(lambda: [0.0, None, None, 0.0, 0])
    for fixation in fixations:
        doc, block = fixation.get("doc"), fixation.get("block")
        if not doc or not block:
            continue
        seconds = (instant(fixation["t_end"]) - instant(fixation["t"])).total_seconds()
        entry = totals[(doc, block)]
        entry[0] += seconds
        entry[1] = entry[1] or fixation["t"]
        entry[2] = fixation["t_end"]
        entry[3] += fixation.get("p") or 0.0
        entry[4] += 1
    looks = []
    for (doc, block), (seconds, t0, t1, p, n) in totals.items():
        if seconds < LOOK_S or (doc, block) in read or (doc, block) not in day.texts:
            continue
        tag, text = day.texts[(doc, block)]
        url, title = day.docs.get(doc, ("", ""))
        looks.append({
            "t": t0, "t_end": t1, "doc": doc, "block": block, "tag": tag, "text": text, "url": url,
            "title": title, "seconds": round(seconds, 2), "p": round(p / n, 2), "fixations": n,
        })  # fmt: skip
    return sorted(looks, key=lambda r: instant(r["t"]))


def _unit(
    day: str, t: str, t_end: str | None, kind: str, text: str, evidence: str, source: str, **extra: object
) -> Record:
    return {
        "day": day,
        "t": t,
        "t_end": t_end,
        "type": kind,
        "text": _clip(text),
        "place": None,
        "floor": None,
        "x": None,
        "y": None,
        "evidence_id": evidence,
        "source_ref": source,
        "attention": extra.get("attention"),
        "fidelity": extra.get("fidelity", "known"),
        "confidence": extra.get("confidence", 1.0),
        "moment_id": None,
        "offset_s": None,
    }


def _where(title: str | None, url: str | None) -> str:
    title = (title or "").strip()
    domain = _domain(url)
    if title and domain:
        return f"{title} ({domain})"
    return title or domain or "a page"


def capture_units(vault: Vault, day: str, capture: CaptureDay | None = None) -> list[Record]:
    """Every searchable unit of a day's capture, in time order."""
    c = capture or read_capture(vault, day)
    log = f"capture/{day}/"
    units: list[Record] = []
    for v in c.visits:
        minutes = v.seconds / 60
        spent = f"{minutes:.0f} min" if minutes >= 1 else f"{v.seconds:.0f} s"
        units.append(_unit(day, v.t0, v.t1, "web.visit", f"Opened {_where(v.title, v.url)} for {spent}",
                           f"capture:visit:{v.first_id}", log + "browser.jsonl"))  # fmt: skip
    for r in c.reads:
        lines = " ".join(line.get("text", "") for line in r.get("lines", []))
        units.append(_unit(day, r["t"], r.get("t_end"), "web.read", f"Read in {_where(r.get('title'), r.get('url'))}: {lines}",
                           f"capture:read:{r['id']}", log + "reading.jsonl", attention=r.get("coverage"),
                           fidelity="estimated", confidence=float(r.get("p") or 0.5)))  # fmt: skip
    for d in c.dwells:
        text = d.get("text") or c.texts.get((d.get("doc"), d.get("block")), ("", ""))[1]
        units.append(_unit(day, d["t"], d.get("t_end"), "web.dwell",
                           f"Stayed {float(d.get('dwell_s') or 0):.0f} s on this in {_where(d.get('title'), d.get('url'))}: {text}",
                           f"capture:dwell:{d.get('dwell') or d['id']}", log + "attention.jsonl",
                           attention=d.get("dwell_s"), fidelity="estimated", confidence=0.8))  # fmt: skip
    for look in c.looks:
        units.append(_unit(day, look["t"], look["t_end"], "web.look",
                           f"Looked at in {_where(look['title'], look['url'])}: {look['text']}",
                           f"capture:look:{look['doc']}:{look['block']}", log + "gaze.jsonl",
                           attention=look["seconds"], fidelity="estimated", confidence=look["p"]))  # fmt: skip
    for s in c.searches:
        units.append(_unit(day, s["t"], None, "web.search", f"Searched {s.get('engine', 'the web')}: {s['query']}",
                           f"capture:search:{s['id']}", log + "browser.jsonl"))  # fmt: skip
    for k in c.clicks:
        target = (
            f" to {_domain(k.get('href'))}" if k.get("href") and _domain(k.get("href")) != _domain(k.get("url")) else ""
        )
        label = k.get("text") or k.get("href") or ""
        units.append(_unit(day, k["t"], None, "web.click", f"Clicked “{label}”{target} in {_where(k.get('title'), k.get('url'))}",
                           f"capture:click:{k['id']}", log + "browser.jsonl"))  # fmt: skip
    for s in c.selections:
        verb = "Copied" if s.get("copied") else "Selected"
        units.append(_unit(day, s["t"], None, "web.selection", f"{verb} in {_where(s.get('title'), s.get('url'))}: “{s['text']}”",
                           f"capture:selection:{s['id']}", log + "browser.jsonl"))  # fmt: skip
    for m in c.media:
        units.append(_unit(day, m["t"], m["t_end"], "web.media",
                           f"Played media for {m['seconds'] / 60:.0f} min in {_where(m.get('title'), m.get('url'))}",
                           f"capture:media:{m['id']}", log + "browser.jsonl"))  # fmt: skip
    units.sort(key=lambda u: (instant(u["t"]), u["type"], u["evidence_id"]))
    return units

"""From the capture logs of one day to activities and search units.

The receiver writes raw records (``services/capture-win``): browser records (``browser.jsonl``),
the text on screen (``blocks.jsonl``), fixations placed on blocks and lines (``gaze.jsonl``),
dwells (``attention.jsonl``), line-by-line reads (``reading.jsonl``) and the app in front
(``windows.jsonl``). Text read in other apps arrives like a web page's, from the screen reader,
under ``app://<program>/<window title>``. This module reads them once and derives:

- page visits: which page was in front, from when to when, from navigation, focus and idle
  records (a tab in front while the session is idle or locked does not count);
- reads: the lines of a block the eyes went along, from the line decoder;
- dwells: runs on one block longer than reading it would take;
- looks: blocks that held the eyes for a while without being read line by line (a figure, a
  table, a heading), from the fixations' blocks;
- searches, clicks on links and buttons, selections and copies, media played;
- time in each app other than the browser (``screen.app``), from the app in front.

Each becomes a unit in the vault database (``ingest.store``), so Ask finds "what did I read about
X yesterday" the way it finds a transcript line. Block texts are sent once per page load, so the
previous day's text snapshots are read too (a page opened before midnight and read after).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from ..time import instant
from ..vault import Record, Vault, file_stamp, stored, text_lines

LOGS = ("browser.jsonl", "blocks.jsonl", "gaze.jsonl", "attention.jsonl", "reading.jsonl", "windows.jsonl")
MIN_VISIT_S = 3.0  # shorter spans in front are tab flipping, not visits
MERGE_GAP_S = 60.0  # the same page back in front within this long continues the visit
LOOK_S = 3.0  # gaze on a block this long (without a line-by-line read) is a look worth keeping
MEDIA_S = 30.0
TEXT_LIMIT = 1200  # characters of a unit's text
MAX_APP_SPAN_S = 2 * 3600.0  # an app in front longer than this with no other record: the receiver stopped
APP = "app://"
# How common programs are called; any other is its file name without ".exe".
APP_NAMES = {
    "code.exe": "Visual Studio Code", "cursor.exe": "Cursor", "devenv.exe": "Visual Studio",
    "pycharm64.exe": "PyCharm", "idea64.exe": "IntelliJ IDEA", "winword.exe": "Word", "excel.exe": "Excel",
    "powerpnt.exe": "PowerPoint", "onenote.exe": "OneNote", "outlook.exe": "Outlook", "olk.exe": "Outlook",
    "ms-teams.exe": "Teams", "teams.exe": "Teams", "acrobat.exe": "Acrobat", "acrord32.exe": "Acrobat Reader",
    "sumatrapdf.exe": "SumatraPDF", "notepad.exe": "Notepad", "notepad++.exe": "Notepad++",
    "explorer.exe": "File Explorer", "windowsterminal.exe": "Terminal", "cmd.exe": "Command Prompt",
    "powershell.exe": "PowerShell", "pwsh.exe": "PowerShell", "claude.exe": "Claude", "chatgpt.exe": "ChatGPT",
    "obsidian.exe": "Obsidian", "notion.exe": "Notion", "slack.exe": "Slack", "discord.exe": "Discord",
    "wechat.exe": "WeChat", "weixin.exe": "WeChat", "qq.exe": "QQ", "telegram.exe": "Telegram",
    "whatsapp.exe": "WhatsApp", "zoom.exe": "Zoom", "spotify.exe": "Spotify", "vlc.exe": "VLC",
    "applicationframehost.exe": "Windows app", "zotero.exe": "Zotero", "matlab.exe": "MATLAB",
    "rstudio.exe": "RStudio", "figma.exe": "Figma", "steam.exe": "Steam",
}  # fmt: skip
LOCK_APPS = frozenset({"lockapp.exe", "logonui.exe"})


def _iter_records(path: Path) -> Iterator[Record]:
    """JSON lines, skipping any that do not parse (a torn append, a hand edit): one bad line must
    not hide a day of capture."""
    if not stored(path).exists():
        return
    for line in text_lines(path):
        if line.strip():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value


def _records(path: Path) -> list[Record]:
    return list(_iter_records(path))


def _block_texts(vault: Vault, path: Path) -> tuple[dict, dict]:
    """Retain only document metadata and block text, keyed by the physical file and write version."""
    source = stored(path)
    key = (source, vault.stamp(path), file_stamp(source))
    cached = vault.capture_texts.get(path)
    if cached is not None and cached[0] == key:
        return cached[1], cached[2]
    docs: dict = {}
    texts: dict = {}
    for record in _iter_records(path):
        doc = record.get("doc")
        if not doc:
            continue
        docs[doc] = (record.get("url", ""), record.get("title", "") or docs.get(doc, ("", ""))[1])
        for block in record.get("blocks", []):
            if block.get("text"):
                texts[(doc, block["id"])] = (block.get("tag", ""), block["text"])
    # A concurrent append/replacement cannot become a cache entry for the earlier stamp.
    if key == (stored(path), vault.stamp(path), file_stamp(stored(path))):
        vault.capture_texts.put_metadata(path, (key, docs, texts))
    return docs, texts


def has_capture_day(vault: Vault, day: str) -> bool:
    """Whether a single valid date has capture logs, without scanning the history."""
    if len(day) != 10:
        return False
    try:
        date.fromisoformat(day)
    except ValueError:
        return False
    folder = vault.root / "capture" / day
    return folder.is_dir() and any(stored(folder / name).exists() for name in LOGS)


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
    """A page's site: its domain, or for a window read in an app (``app://code.exe/...``) the program."""
    host = urlsplit(url or "").hostname or ""
    return host.removeprefix("www.")


def app_name(program: str) -> str:
    program = program.casefold()
    return APP_NAMES.get(program) or program.removesuffix(".exe")


def site_name(url: str | None) -> str:
    """How a site is called: its domain, or the app's name for a window read in an app."""
    domain = _domain(url)
    return app_name(domain) if (url or "").startswith(APP) and domain else domain


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
class AppSpan:
    """An app other than the browser in front, from ``t0`` to ``t1``. ``title`` is empty for an app
    whose windows are private (a password manager)."""

    app: str
    title: str
    url: str
    t0: str
    t1: str
    seconds: float
    first_id: str
    private: bool = False


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
    apps: list[AppSpan] = field(default_factory=list)  # apps other than the browser in front
    texts: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)  # (doc, block) -> (tag, text)
    docs: dict[str, tuple[str, str]] = field(default_factory=dict)  # doc -> (url, title)


def read_capture(vault: Vault, day: str) -> CaptureDay:
    folder = vault.root / "capture" / day
    out = CaptureDay(day)
    previous = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    for path in (vault.root / "capture" / previous / "blocks.jsonl", folder / "blocks.jsonl"):
        docs, texts = _block_texts(vault, path)
        for doc, (url, title) in docs.items():
            out.docs[doc] = (url, title or out.docs.get(doc, ("", ""))[1])
        out.texts.update(texts)
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
    fixations = _records(folder / "gaze.jsonl")
    out.looks = _looks(fixations, out)
    windows = sorted(_records(folder / "windows.jsonl"), key=lambda r: instant(r["t"]))
    if windows:
        # The last thing heard that day closes the app in front at the end.
        last = [windows[-1]["t"]] + ([browser[-1]["t"]] if browser else [])
        last += [f["t_end"] for f in fixations[-1:] if f.get("t_end")]
        out.apps = _apps(windows, max(last, key=instant))
    return out


def _apps(windows: list[Record], last_seen: str) -> list[AppSpan]:
    """Spans of each app in front, from one ``window`` record to the next record (another window,
    the browser, away, paused), merged when the same window comes back within ``MERGE_GAP_S``."""
    spans: list[AppSpan] = []
    current: AppSpan | None = None

    def close(t: str) -> None:
        nonlocal current
        if current is not None:
            seconds = min((instant(t) - instant(current.t0)).total_seconds(), MAX_APP_SPAN_S)
            if seconds > 0:
                current.seconds = seconds
                current.t1 = (instant(current.t0) + timedelta(seconds=seconds)).isoformat()
                spans.append(current)
        current = None

    for record in windows:
        t = record["t"]
        if record.get("kind") == "away" and record.get("since"):
            # Away after minutes without input: the app was not in use since the last input.
            t = max(record["since"], current.t0, key=instant) if current is not None else t
        close(t)
        app = str(record.get("app") or "").casefold()
        if record.get("kind") != "window" or not app or record.get("browser") or app in LOCK_APPS:
            continue
        private = bool(record.get("private"))
        title = "" if private else str(record.get("title") or "")
        url = record.get("url") or f"{APP}{app}/"
        current = AppSpan(app, title, url, t, t, 0.0, record.get("id") or t, private)
    if current is not None and instant(last_seen) > instant(current.t0):
        close(last_seen)
    merged: list[AppSpan] = []
    for span in spans:
        last = merged[-1] if merged else None
        if (
            last is not None
            and (last.app, last.title) == (span.app, span.title)
            and (instant(span.t0) - instant(last.t1)).total_seconds() <= MERGE_GAP_S
        ):
            last.t1, last.seconds = span.t1, last.seconds + span.seconds
        else:
            merged.append(span)
    return [span for span in merged if span.seconds >= MIN_VISIT_S]


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
    domain = site_name(url)
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
    for a in c.apps:
        minutes = a.seconds / 60
        spent = f"{minutes:.0f} min" if minutes >= 1 else f"{a.seconds:.0f} s"
        units.append(_unit(day, a.t0, a.t1, "screen.app", f"Used {app_name(a.app)} for {spent}" + (f": {a.title}" if a.title else ""),
                           f"capture:app:{a.first_id}", log + "windows.jsonl"))  # fmt: skip
    units.sort(key=lambda u: (instant(u["t"]), u["type"], u["evidence_id"]))
    return units

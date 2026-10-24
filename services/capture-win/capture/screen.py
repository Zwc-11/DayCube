"""Reading the whole screen: the text of whatever window the eyes are on, in any app.

The extension knows the text of web pages exactly. Everything else on screen (an editor, a PDF
reader, a terminal, the Claude app, Word, a chat app) it cannot see. This reader fills the gap on
Windows, inside the receiver:

1. **Which window.** Twice a second it takes the newest gaze (the last fixation, corrected by
   clicks like every fixation, or the live point) and finds the top-level window shown at that
   point: the first in z-order that is visible there (not minimized, not on another virtual
   desktop, not a click-through overlay such as the gaze ring). The window the eyes are on, not
   the one with focus: reading a PDF on the left while the editor on the right has focus is
   reading the PDF. (``WindowFromPoint`` would skip a window disabled by its own dialog.)
2. **Its text.** It captures that window's pixels as they are on screen and reads them with the
   OCR built into Windows (``Windows.Media.Ocr``, local; RapidOCR where that is missing). Only when
   they changed since the last read (a thumbnail of every 8th pixel is compared), at most once a
   second (less often while the reads find no new text, as on a video), and at least every 30 s.
   Lines are grouped into paragraphs.
3. **As a page.** The window becomes a page like a browser tab: a ``blocks`` record with where it
   sits on the monitor and its paragraphs with their lines, handed to the receiver in process.
   From there it is the same as a web page: fixations are placed on paragraphs and lines, dwells
   fire, ``reading.jsonl`` follows the lines read, and search, notes and the graph see it, under
   ``app://<program>/<window title>``. Scrolling is followed: a paragraph seen before is
   recognised by its text (the same letters and digits, or mostly the same trigrams: OCR reads a
   moved paragraph a little differently) and keeps its id and its place on the "page" (the
   window's content), so reading goes on across a scroll, and its text is sent once. A retitled
   window stays one page; one whose content was replaced (nothing matches) becomes a new page.

Which app is in front is logged too (``windows.jsonl``), for time spent per app, with ``away``
when there has been no input for 5 minutes and no gaze.

Privacy: screenshots never leave memory; only the recognised text is kept, as the extension keeps
page text. Browsers are never read (the extension covers their pages, with its exclusions), nor
password managers, Windows' sign-in, lock and permission screens, the taskbar and Start, or a
window whose title says it is private (InPrivate, Incognito). More apps can be excluded in
``vault/capture/screen.json`` (``{"enabled": true, "exclude_apps": ["slack.exe"]}``);
``python -m capture screen off`` turns reading off. Pausing recording stops it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sys
import threading
import time
import unicodedata
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import numpy as np

log = logging.getLogger("capture.screen")

EVERY_S = 0.5
GAZE_FRESH_S = 3.0  # the newest gaze must be this recent to pick a window
VIEW_EVERY_S = 2.0  # a window being read is re-announced this often (the receiver forgets pages after 12 s)
MIN_READ_S = 1.0  # at most one OCR a second; doubled (up to 16 s) while reads find no new text
RECHECK_S = 30.0  # read the window again at least this often while the eyes are on it
THUMB = 8  # the change check samples every 8th pixel
CHANGED_SHARE = 0.003  # share of sampled pixels that changed by more than CHANGED_GREY: the window changed
CHANGED_GREY = 24
AWAY_S = 300.0  # no keyboard or mouse input this long, and no gaze: away
SCROLL_ORIGIN = 100_000.0  # where the first read of a window sits on its "page": room to scroll up
MAX_BLOCKS, MAX_LINES, MAX_TEXT = 150, 60, 1000  # the receiver's limits for a page
KNOWN_LIMIT = 3000  # paragraphs remembered per window

BROWSERS = frozenset({
    "opera.exe", "opera_gx.exe", "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "vivaldi.exe",
    "arc.exe", "iexplore.exe", "chromium.exe", "librewolf.exe", "waterfox.exe", "zen.exe",
})  # fmt: skip
PRIVATE_APPS = frozenset({
    "1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "dashlane.exe", "lastpass.exe",
    "enpass.exe", "nordpass.exe", "proton pass.exe", "roboform.exe", "keeper.exe",
    "consent.exe", "credentialuibroker.exe", "securityhealthsystray.exe",
})  # fmt: skip
LOCK_APPS = frozenset({"lockapp.exe", "logonui.exe"})
SHELL_APPS = frozenset({
    "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "shellhost.exe",
})  # fmt: skip
SHELL_CLASSES = frozenset({
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW", "NotifyIconOverflowWindow",
    "TopLevelWindowForOverflowXamlIsland", "XamlExplorerHostIslandWindow", "MultitaskingViewFrame",
    "ForegroundStaging", "Windows.UI.Core.CoreWindow",
})  # fmt: skip
POPUP_CLASSES = frozenset({"#32768", "tooltips_class32", "SysShadow"})  # menus, tooltips, shadows
PRIVATE_TITLES = ("inprivate", "incognito", "private browsing", "private window")
OWN_TITLES = ("daycube gaze ring", "daycube calibration")


@dataclass(frozen=True)
class Window:
    hwnd: int
    app: str  # the program's file name, lower case: "code.exe"
    title: str
    rect: tuple[int, int, int, int]  # left, top, width, height in physical screen pixels
    cls: str = ""  # the window class


@dataclass(frozen=True)
class OcrLine:
    text: str
    rect: tuple[float, float, float, float]  # left, top, width, height in image pixels
    words: int


class Engine(Protocol):
    name: str

    def read(self, image: np.ndarray, key: str | None = None) -> list[OcrLine]:
        """The lines in a window's image; ``key`` names the window's page (an engine may keep
        per-window choices)."""
        ...


class System(Protocol):
    def monitor(self) -> tuple[int, int, int, int]: ...
    def window_at(self, x: int, y: int) -> Window | None: ...
    def foreground(self) -> Window | None: ...
    def capture(self, rect: tuple[int, int, int, int], window: Window) -> np.ndarray: ...
    def idle_s(self) -> float: ...


# -- which windows are read ----------------------------------------------------------------------


def settings(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def why_not(window: Window, extra: frozenset[str] = frozenset()) -> str | None:
    """Why this window's text is not read, or None to read it."""
    title = window.title.casefold()
    if window.app in BROWSERS:
        return "browser"  # its pages come from the extension, with the extension's exclusions
    if window.app in LOCK_APPS:
        return "locked"
    if window.app in PRIVATE_APPS or window.app in extra:
        return "excluded"
    if window.app in SHELL_APPS or window.cls in SHELL_CLASSES:
        return "shell"
    if window.cls in POPUP_CLASSES:
        return "popup"
    if any(word in title for word in PRIVATE_TITLES):
        return "private"
    if any(title.startswith(own) for own in OWN_TITLES):
        return "daycube"
    if window.rect[2] < 80 or window.rect[3] < 40:
        return "too small"
    return None


# -- from OCR lines to paragraphs -----------------------------------------------------------------


def blocks_of(lines: list[OcrLine]) -> list[dict]:
    """OCR lines grouped into paragraphs: a line joins the paragraph whose last line sits right above
    it (a gap under 0.8 line heights) and overlaps it sideways by half the narrower line; otherwise
    it starts one, so side-by-side columns stay apart. Rectangles are in image pixels. A block's id
    comes from its text, so a paragraph keeps its id when the window scrolls or is read again."""
    median = _median_height(lines)
    open_: list[dict] = []
    for line in sorted((ln for ln in lines if ln.text.strip()), key=lambda ln: (ln.rect[1], ln.rect[0])):
        x, y, w, h = line.rect
        home = None
        for block in open_:
            lx, ly, lw, lh = block["last"]
            gap = y - (ly + lh)
            overlap = min(x + w, lx + lw) - max(x, lx)
            if -0.5 * lh <= gap <= 0.8 * max(lh, h) and overlap >= 0.5 * min(w, lw) and len(block["lines"]) < MAX_LINES:
                home = block
                break
        if home is None:
            home = {"lines": [], "words": 0}
            open_.append(home)
        home["lines"].append(line)
        home["last"] = line.rect
        home["words"] += line.words
    out = []
    seen: dict[str, int] = {}
    for block in open_:
        text = " ".join(" ".join(ln.text.split()) for ln in block["lines"])
        left = min(ln.rect[0] for ln in block["lines"])
        top = min(ln.rect[1] for ln in block["lines"])
        right = max(ln.rect[0] + ln.rect[2] for ln in block["lines"])
        bottom = max(ln.rect[1] + ln.rect[3] for ln in block["lines"])
        digest = hashlib.blake2b(text.encode("utf-8"), digest_size=5).hexdigest()
        n = seen[digest] = seen.get(digest, 0) + 1
        heading = len(block["lines"]) == 1 and block["lines"][0].rect[3] > 1.4 * median
        out.append({
            "id": f"o{digest}" + (f"-{n}" if n > 1 else ""),
            "tag": "h" if heading else "p",
            "text": text[:MAX_TEXT],
            "words": block["words"],
            "rect": [round(left, 1), round(top, 1), round(right - left, 1), round(bottom - top, 1)],
            "lines": [{"rect": [round(v, 1) for v in ln.rect], "text": ln.text[:300]} for ln in block["lines"]],
        })  # fmt: skip
    if len(out) > MAX_BLOCKS:  # a busy window: keep the paragraphs with the most words
        keep = set(sorted(range(len(out)), key=lambda i: -out[i]["words"])[:MAX_BLOCKS])
        out = [b for i, b in enumerate(out) if i in keep]
    return out


def _median_height(lines: list[OcrLine]) -> float:
    heights = sorted(ln.rect[3] for ln in lines) or [0.0]
    return heights[len(heights) // 2]


@dataclass
class Known:
    """A paragraph already sent for a window: its text as compared, and where it is on the page."""

    norm: str
    grams: frozenset[str]
    left: float
    page_top: float
    lines: int
    seen: int  # the read it was last seen in


def normal(text: str) -> str:
    """Letters and digits only, lower case: what two OCR reads of one paragraph agree on (they
    differ in spaces and punctuation more than in letters)."""
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def trigrams(norm: str) -> frozenset[str]:
    return frozenset(norm[i : i + 3] for i in range(len(norm) - 2)) or frozenset({norm})


SIMILAR = 0.6  # trigram overlap (Jaccard) of two reads of one paragraph
RECENT_READS = 3  # paragraphs seen this many reads ago or later are compared by trigrams


def identify(
    blocks: list[dict], known: dict[str, Known], scroll: float, read: int
) -> tuple[list[str | None], float | None]:
    """Which known paragraph each block is (None: new), and the scroll that puts the matched ones
    where they were on the page (None: nothing matched, or the matches disagree: an edit, not a
    scroll). First by the same letters and digits (the nearest on the page when several), then by
    trigram overlap among the paragraphs of the last few reads."""
    norms = [normal(b["text"]) for b in blocks]
    ids: list[str | None] = [None] * len(blocks)
    taken: set[str] = set()
    by_norm: dict[str, list[str]] = {}
    for key, entry in known.items():
        by_norm.setdefault(entry.norm, []).append(key)
    for i, block in enumerate(blocks):
        candidates = [k for k in by_norm.get(norms[i], ()) if k not in taken]
        if candidates and norms[i]:
            best = min(candidates, key=lambda k: abs(known[k].page_top - block["rect"][1] - scroll))
            ids[i] = best
            taken.add(best)
    recent = [k for k, entry in known.items() if entry.seen >= read - RECENT_READS and k not in taken]
    for i in range(len(blocks)):
        if ids[i] is not None or len(norms[i]) < 6:
            continue
        grams, size = trigrams(norms[i]), len(norms[i])
        best, best_score = None, SIMILAR
        for key in recent:
            entry = known[key]
            if key in taken or min(size, len(entry.norm)) < 0.6 * max(size, len(entry.norm)):
                continue
            score = len(grams & entry.grams) / len(grams | entry.grams)
            if score >= best_score:
                best, best_score = key, score
        if best is not None:
            ids[i] = best
            taken.add(best)
    scrolls = sorted(known[k].page_top - blocks[i]["rect"][1] for i, k in enumerate(ids) if k is not None)
    if not scrolls:
        return ids, None
    middle = scrolls[len(scrolls) // 2]
    agree = [s for s in scrolls if abs(s - middle) <= 4]
    return ids, (round(sum(agree) / len(agree), 1) if 2 * len(agree) > len(scrolls) else None)


def thumbnail(image: np.ndarray) -> np.ndarray:
    """Grey, every 8th pixel: enough to tell a scroll or new text from a blinking cursor."""
    return image[::THUMB, ::THUMB, :3].astype(np.int16).sum(axis=2) // 3


def changed(a: np.ndarray | None, b: np.ndarray) -> bool:
    if a is None or a.shape != b.shape:
        return True
    return float((np.abs(a - b) > CHANGED_GREY).mean()) >= CHANGED_SHARE


# -- the reader -------------------------------------------------------------------------------------


@dataclass
class Current:
    """The window being read, as a page."""

    window: Window
    doc: str
    url: str
    thumb: np.ndarray | None = None
    read_at: float = -1e9
    announced_at: float = -1e9
    backoff: float = 1.0  # MIN_READ_S times this between reads; doubles while reads find nothing new
    scroll: float = SCROLL_ORIGIN  # where the window's top sits on its page
    known: dict[str, Known] = field(default_factory=dict)  # paragraphs sent, by id
    reads: int = 0
    geometry: dict = field(default_factory=dict)
    generation: int = 0  # how many times the window's content was replaced (another file, chat, page)


class ScreenReader(threading.Thread):
    """Runs beside the receiver (``receiver``: ``capture.api.Receiver``). ``engine``: the OCR, or a
    function making it, called on the reader's own thread (WinRT objects are made where they are used)."""

    def __init__(
        self,
        receiver: Any,
        system: System,
        engine: Engine | Callable[[], Engine],
        settings_file: Path,
        clock: Callable[[], float] = time.time,
        every_s: float = EVERY_S,
    ) -> None:
        super().__init__(name="screen reader", daemon=True)
        self.receiver, self.system = receiver, system
        self.engine: Engine | None = None if callable(engine) and not hasattr(engine, "read") else engine  # type: ignore[assignment]
        self.make_engine = engine if self.engine is None else None
        self.settings_file, self.clock, self.every_s = settings_file, clock, every_s
        self.stopping = threading.Event()
        self.current: Current | None = None
        self.front: Window | None = None  # the app in front, as last logged
        self.away = False
        self.skipped: str | None = None  # why the window under the gaze is not read
        self.reads: deque[tuple[float, float, str]] = deque(maxlen=2000)  # (time, ms, app) of OCR runs
        self.error: str | None = None
        self.delivery_error: str | None = None
        self._options: tuple[bool, frozenset[str]] | None = None
        self._options_stamp: tuple[int, int] | None = None
        self.session = uuid.uuid4().hex  # documents of this run are its own
        self.document_serial = 0

    def run(self) -> None:
        try:
            self._run()
        finally:
            self.leave(self.clock())
            close = getattr(self.system, "close", None)
            if close is not None:
                close()  # mss owns thread-local handles: release them on the creating thread

    def _run(self) -> None:
        if self.engine is None:
            try:
                self.engine = self.make_engine()
                log.info("reading the screen with %s OCR", self.engine.name)
            except Exception as error:  # noqa: BLE001 - no OCR here: say why in /status and the doctor
                self.error = f"no OCR: {error}"[:300]
                log.warning("not reading the screen: %s", self.error)
                return
        while not self.stopping.wait(self.every_s):
            try:
                self.step()
                self.error = None
            except Exception as error:  # noqa: BLE001 - logged; the next round tries again
                message = f"{type(error).__name__}: {error}"[:200]
                if self.error != message:
                    log.exception("reading the screen failed")
                self.error = message

    def stop(self) -> None:
        self.stopping.set()

    def options(self) -> tuple[bool, frozenset[str]]:
        """``screen.json``: whether to read, and apps never to read; read again when it changes."""
        try:
            stat = self.settings_file.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        if stamp != self._options_stamp or self._options is None:
            options = settings(self.settings_file)
            extra = frozenset(str(a).casefold() for a in options.get("exclude_apps", []) if isinstance(a, str))
            self._options, self._options_stamp = (bool(options.get("enabled", True)), extra), stamp
        return self._options

    def state(self, window_s: float = 600.0) -> dict:
        now = self.clock()
        recent = [(t, ms, app) for t, ms, app in list(self.reads) if now - t <= window_s]
        current = self.current
        return {
            "running": self.is_alive(),
            "engine": getattr(self.engine, "name", None),
            "language": getattr(self.engine, "language", None),
            "enabled": self.options()[0],
            "reading": None if current is None else {"app": current.window.app, "title": current.window.title[:120]},
            "skipped": self.skipped,
            "away": self.away,
            "reads_10min": len(recent),
            "apps_10min": sorted({app for _, _, app in recent}),
            "ocr_ms": round(float(np.median([ms for _, ms, _ in recent]))) if recent else None,
            "error": self.error or self.delivery_error,
        }

    # -- one round ---------------------------------------------------------------------------------

    def step(self) -> None:
        now = self.clock()
        on, extra = self.options()
        if not on or self.receiver.paused:
            self.skipped = "off" if not on else "paused"
            if self.front is not None and on is False:
                self.receiver.app_window(_entry(now, "away") | {"why": "off"})  # a pause logs itself
            self.front, self.away = None, False  # logged again when it comes back on
            self.leave(now)
            return
        gaze = self.receiver.gaze_now(GAZE_FRESH_S)
        self.log_front(now, extra, gaze is not None)
        if gaze is None:
            self.skipped = "no gaze"
            self.leave(now)
            return
        left, top, width, height = monitor = self.system.monitor()
        window = self.system.window_at(round(left + gaze[0] * width), round(top + gaze[1] * height))
        reason = "no window" if window is None else why_not(window, extra)
        self.skipped = reason
        if window is None or reason is not None:
            self.leave(now)
            return
        current = self.current
        if current is None or (current.window.hwnd, current.window.app) != (window.hwnd, window.app):
            self.leave(now)
            current = self.current = self.open(window)
        elif current.window != window:
            # The same window, retitled ("● unsaved", an unread count, another file): the same page
            # until its content turns out to be another (``submit``), under the new title.
            if current.window.rect != window.rect:
                current.thumb = None  # moved or resized: read it again now
                current.read_at = -1e9
            current.window, current.url = window, app_url(window.app, window.title.strip() or window.app)
        rect = _clip(window.rect, monitor)
        if rect is None:  # off the tracker's monitor
            self.leave(now)
            return
        image = self.system.capture(rect, window)
        thumb = thumbnail(image)
        due = now - current.read_at >= RECHECK_S or (
            changed(current.thumb, thumb) and now - current.read_at >= MIN_READ_S * current.backoff
        )
        if due:
            started = time.perf_counter()
            lines = self.engine.read(image, current.doc)
            self.reads.append((now, (time.perf_counter() - started) * 1000, window.app))
            current.thumb, current.read_at = thumb, now
            self.submit(current, rect, monitor, blocks_of(lines), now)
        elif current.thumb is not None and now - current.announced_at >= VIEW_EVERY_S:
            current.geometry = _geometry(rect, monitor, current.scroll)
            if self.send(self.record(current, "view", now)):
                current.announced_at = now

    def submit(
        self, current: Current, rect: tuple[int, int, int, int], monitor: tuple[int, int, int, int],
        blocks: list[dict], now: float,
    ) -> None:  # fmt: skip
        """A new read of the window as a ``blocks`` record: the scroll since the last read, each
        paragraph's text only the first time it is seen, its lines (page coordinates) when new or
        moved on the page."""
        read = current.reads + 1
        ids, scroll = identify(blocks, current.known, current.scroll, read)
        if blocks and current.known and all(key is None for key in ids):
            # Nothing on screen was there before: another file, chat or page in the same window. A
            # new page, so its paragraphs do not land where the old ones were (and the receiver ends
            # what was being read on the old one).
            current = self.current = self.open(current.window, current.generation + 1)
            read = 1
            ids = [None] * len(blocks)
        if scroll is not None:
            current.scroll = max(0.0, scroll)
        new_text = any(key is None for key in ids)
        current.backoff = 1.0 if new_text else min(current.backoff * 2, 16.0)
        out, updates, taken = [], {}, set(ids)
        for block, key in zip(blocks, ids, strict=True):
            left, page_top = block["rect"][0], block["rect"][1] + current.scroll
            old = current.known.get(key) if key is not None else None
            if key is None:
                norm = normal(block["text"])
                base = "o" + hashlib.blake2b(norm.encode("utf-8"), digest_size=5).hexdigest()
                key, n = base, 1
                while key in current.known or key in taken or key in updates:
                    n += 1
                    key = f"{base}-{n}"
                old = None
            entry = {"id": key, "tag": block["tag"], "rect": block["rect"]}
            if old is None:
                entry |= {"text": block["text"], "words": block["words"]}
            moved = old is None or abs(old.left - left) > 3 or abs(old.page_top - page_top) > 3
            if moved or old.lines != len(block["lines"]):
                entry["lines"] = [
                    {"rect": [r[0], round(r[1] + current.scroll, 1), r[2], r[3]], "text": line["text"]}
                    for line in block["lines"]
                    for r in [line["rect"]]
                ]
                norm = old.norm if old is not None else normal(block["text"])
                grams = old.grams if old is not None else trigrams(norm)
                updates[key] = Known(norm, grams, left, page_top, len(block["lines"]), read)
            elif old is not None:
                updates[key] = Known(old.norm, old.grams, old.left, old.page_top, old.lines, read)
            out.append(entry)
        current.geometry = _geometry(rect, monitor, current.scroll)
        if self.send(self.record(current, "blocks", now) | {"blocks": out}):
            current.known |= updates
            current.reads, current.announced_at = read, now
            if len(current.known) > KNOWN_LIMIT:  # a very long document: forget the longest unseen
                for key in sorted(current.known, key=lambda k: current.known[k].seen)[
                    : len(current.known) - KNOWN_LIMIT
                ]:
                    del current.known[key]
        else:
            # The pixels were read, but their text was not stored. Retry even if those pixels
            # stay unchanged, at the normal OCR rate; a heartbeat cannot acknowledge this read.
            current.thumb, current.backoff = None, 1.0

    def open(self, window: Window, generation: int = 0) -> Current:
        title = window.title.strip() or window.app
        # After leaving, the window may show another document, and HWNDs can be reused. Only
        # the active lifetime has the text/scroll history needed to preserve document identity.
        self.document_serial += 1
        key = f"{self.session}|{self.document_serial}|{window.hwnd}|{window.app}|{generation}"
        doc = "scr-" + hashlib.blake2b(key.encode(), digest_size=10).hexdigest()
        current = Current(window, doc, app_url(window.app, title), generation=generation)
        if generation and self.current is not None:  # the same window, just read
            current.thumb, current.read_at = self.current.thumb, self.current.read_at
        return current

    def leave(self, now: float) -> None:
        """The eyes left the window (or reading stopped): the receiver stops placing gaze on it."""
        current, self.current = self.current, None
        if current is not None and current.geometry:
            self.send(self.record(current, "view", now) | {"hidden": True})

    def record(self, current: Current, kind: str, now: float) -> dict:
        window = current.window
        return {
            "id": uuid.uuid4().hex, "t": datetime.fromtimestamp(now, UTC).isoformat(), "kind": kind,
            "tab": -window.hwnd, "window": -window.hwnd, "url": current.url, "title": window.title[:1000],
            "doc": current.doc, "geometry": current.geometry,
        }  # fmt: skip

    def send(self, record: dict) -> bool:
        try:
            accepted = self.receiver.screen_records([record]) > 0
            self.delivery_error = None if accepted else "screen record not accepted"
            return accepted
        except Exception as error:  # noqa: BLE001 - paused between the check and now
            self.delivery_error = f"{type(error).__name__}: {error}"[:200]
            log.debug("screen record not taken: %s", error)
            return False

    def log_front(self, now: float, extra: frozenset[str], gazing: bool) -> None:
        """Which app is in front, when it changes (``windows.jsonl``), and when the person is away:
        the lock screen, or no input for ``AWAY_S`` and no gaze (reading without touching anything
        is not away while the tracker sees the eyes)."""
        front = self.system.foreground()
        idle = 0.0 if gazing else self.system.idle_s()
        locked = front is not None and front.app in LOCK_APPS
        if locked or idle >= AWAY_S:
            if not self.away:
                self.away = True
                away = _entry(now, "away") | {"why": "locked" if locked else "idle"}
                if not locked:
                    away["since"] = datetime.fromtimestamp(now - idle, UTC).isoformat()
                self.receiver.app_window(away)
            self.front = None  # logged again on return
            return
        self.away = False
        if front is None:  # the desktop, or nothing, has focus
            if self.front is not None:
                self.receiver.app_window(_entry(now, "away") | {"why": "no window"})
                self.front = None
            return
        if self.front is not None and (front.hwnd, front.title) == (self.front.hwnd, self.front.title):
            return
        self.front = front
        reason = why_not(front, extra)
        record = _entry(now, "window") | {"app": front.app}
        if reason in (None, "too small"):
            title = front.title.strip()
            record |= {"title": title[:1000], "url": app_url(front.app, title or front.app)}
        elif reason == "browser":
            record["browser"] = True  # the extension records its pages
        else:
            record["private"] = True  # an app, never its window's title
        self.receiver.app_window(record)


def _entry(now: float, kind: str) -> dict:
    return {"id": uuid.uuid4().hex, "t": datetime.fromtimestamp(now, UTC).isoformat(), "kind": kind}


def app_url(app: str, title: str) -> str:
    return f"app://{quote(app, safe='')}/{quote(title, safe='')}"[:4096]


def _clip(rect: tuple[int, int, int, int], monitor: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """The part of a window on the tracker's monitor (others are not tracked)."""
    x0, y0 = max(rect[0], monitor[0]), max(rect[1], monitor[1])
    x1 = min(rect[0] + rect[2], monitor[0] + monitor[2])
    y1 = min(rect[1] + rect[3], monitor[1] + monitor[3])
    return (x0, y0, x1 - x0, y1 - y0) if x1 - x0 >= 80 and y1 - y0 >= 40 else None


def _geometry(rect: tuple[int, int, int, int], monitor: tuple[int, int, int, int], scroll: float) -> dict:
    """The window as a page's viewport, all in physical pixels (one per "CSS" pixel)."""
    return {
        "vx": rect[0] - monitor[0], "vy": rect[1] - monitor[1], "scale": 1.0, "inner_w": rect[2],
        "inner_h": rect[3], "screen_w": monitor[2], "screen_h": monitor[3], "primary": True, "exact": True,
        "scroll_y": round(scroll, 1), "scroll_h": round(scroll + rect[3], 1),
    }  # fmt: skip


# -- OCR engines --------------------------------------------------------------------------------------


CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
CJK_TAGS = ("zh", "ja", "ko")
LATIN_SHARE, CJK_SHARE = 0.05, 0.5  # a window's CJK share below: Latin text; above: CJK text; between: both
RECHECK_EVERY = 5  # reads of a window between checks of which script it shows


def cjk_share(text: str) -> float:
    """CJK characters among the letters and digits."""
    letters = sum(ch.isalnum() for ch in text)
    return len(CJK.findall(text)) / letters if letters else 0.0


def join_words(words: list[str]) -> str:
    """A line's words as text: spaced, except between two CJK characters (Chinese and Japanese
    have no spaces; Windows OCR gives each character as a word)."""
    out = ""
    for word in words:
        if out and word and not (CJK.match(out[-1]) and CJK.match(word[0])):
            out += " "
        out += word
    return out


def tidy(text: str) -> str:
    """Full-width forms a CJK recogniser puts in Latin text ("10 ． 0", "E ：") as plain ASCII."""
    return unicodedata.normalize("NFKC", text) if cjk_share(text) < 0.3 else text


def _overlaps(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    cx, cy = a[0] + a[2] / 2, a[1] + a[3] / 2
    return b[0] <= cx <= b[0] + b[2] and b[1] <= cy <= b[1] + b[3]


class Scripts:
    """Which recogniser reads a window when Windows has one for CJK and one for Latin text (a
    Chinese Windows reading English, or the reverse). A CJK recogniser reads English badly
    ("PowerSheII", "t0", "10 ． 0") and a Latin one cannot read Chinese at all, so each window is
    judged by the CJK share of what the CJK recogniser reads in it: Latin (one Latin pass), CJK
    (one CJK pass), or both (a Chinese interface around English text: both passes, each line from
    the recogniser of its script). The judgement is redone every ``RECHECK_EVERY`` reads."""

    def __init__(
        self,
        cjk: Callable[[np.ndarray], list[OcrLine]] | None,
        latin: Callable[[np.ndarray], list[OcrLine]] | None,
    ) -> None:
        self.cjk, self.latin = cjk, latin
        self.modes: dict[str, tuple[str, int]] = {}

    def read(self, image: np.ndarray, key: str | None = None) -> list[OcrLine]:
        if self.cjk is None or self.latin is None:
            return (self.cjk or self.latin)(image)  # type: ignore[misc]
        key = key or ""
        mode, count = self.modes.get(key, ("", 0))
        if mode and count % RECHECK_EVERY:
            self.modes[key] = (mode, count + 1)
            return self._read(mode, image, None)
        cjk = self.cjk(image)
        share = cjk_share("".join(line.text for line in cjk))
        mode = "latin" if share < LATIN_SHARE else "cjk" if share >= CJK_SHARE else "both"
        if len(self.modes) > 500:
            self.modes.clear()
        self.modes[key] = (mode, 1)
        return self._read(mode, image, cjk)

    def _read(self, mode: str, image: np.ndarray, cjk: list[OcrLine] | None) -> list[OcrLine]:
        if mode == "latin":
            return self.latin(image)  # type: ignore[misc]
        cjk = cjk if cjk is not None else self.cjk(image)  # type: ignore[misc]
        if mode == "cjk":
            return cjk
        kept = [line for line in cjk if cjk_share(line.text) >= 0.3]
        latin = [line for line in self.latin(image) if not any(_overlaps(line.rect, k.rect) for k in kept)]  # type: ignore[misc]
        return sorted(kept + latin, key=lambda line: (line.rect[1], line.rect[0]))


class WindowsOcr:
    """Windows' own OCR (``Windows.Media.Ocr`` through PyWinRT): local, a few hundred milliseconds a
    window. In the first language of the Windows profile that has OCR, and, when that is Chinese,
    Japanese or Korean and an English recogniser is installed too (or the reverse), both: see
    ``Scripts``."""

    name = "windows"

    def __init__(self) -> None:
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine

        self._bitmap, self._format, self._alpha = SoftwareBitmap, BitmapPixelFormat.BGRA8, BitmapAlphaMode.IGNORE
        main = OcrEngine.try_create_from_user_profile_languages()
        if main is None:
            raise RuntimeError("no OCR language installed (Settings > Time & language > Language & region)")
        available = {language.language_tag: language for language in OcrEngine.available_recognizer_languages}
        self.languages = sorted(available)
        tag = main.recognizer_language.language_tag
        is_cjk = tag.lower().startswith(CJK_TAGS)
        other = next(
            (t for t in sorted(available, key=lambda t: (not t.lower().startswith("en-us"), t))
             if t.lower().startswith(CJK_TAGS) != is_cjk and (is_cjk is False or t.lower().startswith("en"))),
            None,
        )  # fmt: skip
        second = OcrEngine.try_create_from_language(available[other]) if other else None
        self.language = tag + (f" + {other}" if second is not None else "")
        self.max_side = int(OcrEngine.max_image_dimension)
        self._loop = asyncio.new_event_loop()
        self._as_buffer: Callable[[np.ndarray], Any] | None = None

        def using(engine: Any) -> Callable[[np.ndarray], list[OcrLine]]:
            return lambda image: self._recognise(engine, image)

        cjk, latin = (main, second) if is_cjk else (second, main)
        self.scripts = Scripts(using(cjk) if cjk is not None else None, using(latin) if latin is not None else None)

    def _buffer(self, pixels: np.ndarray) -> Any:
        """The BGRA pixels as an IBuffer. PyWinRT takes an object with the buffer protocol; older
        versions want bytes or a DataWriter's buffer. The first that works is kept."""
        if self._as_buffer is not None:
            return self._as_buffer(pixels)

        def writer(array: np.ndarray) -> Any:
            from winrt.windows.storage.streams import DataWriter

            out = DataWriter()
            out.write_bytes(array.tobytes())
            return out.detach_buffer()

        last: Exception | None = None
        for way in (lambda a: a.reshape(-1), lambda a: a.tobytes(), writer):
            try:
                buffer = way(pixels)
                self._bitmap.create_copy_from_buffer(
                    buffer, self._format, pixels.shape[1], pixels.shape[0], self._alpha
                )
            except (TypeError, ValueError) as error:
                last = error
                continue
            self._as_buffer = way
            return buffer
        raise RuntimeError(f"PyWinRT takes none of the pixel buffers: {last}")

    def read(self, image: np.ndarray, key: str | None = None) -> list[OcrLine]:
        """``key``: the window (its page), so each window keeps the recogniser that suits it."""
        return self.scripts.read(image, key)

    def _recognise(self, engine: Any, image: np.ndarray) -> list[OcrLine]:
        step = 1
        while max(image.shape[0], image.shape[1]) / step > self.max_side:
            step += 1  # wider than OCR takes (about 10,000 px): rare
        pixels = np.ascontiguousarray(image[::step, ::step], dtype=np.uint8)
        height, width = pixels.shape[:2]
        bitmap = self._bitmap.create_copy_from_buffer(self._buffer(pixels), self._format, width, height, self._alpha)

        async def recognise() -> Any:
            return await engine.recognize_async(bitmap)

        result = self._loop.run_until_complete(recognise())
        out = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            boxes = [word.bounding_rect for word in words]
            x0, y0 = min(b.x for b in boxes) * step, min(b.y for b in boxes) * step
            x1 = max(b.x + b.width for b in boxes) * step
            y1 = max(b.y + b.height for b in boxes) * step
            text = tidy(join_words([word.text for word in words]))
            out.append(OcrLine(text, (x0, y0, x1 - x0, y1 - y0), len(words)))
        return out


class RapidOcr:
    """RapidOCR (PaddleOCR's models on ONNX Runtime): works anywhere, slower (about a second a
    window). Installed with ``uv sync --extra ocr``."""

    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR()

    def read(self, image: np.ndarray, key: str | None = None) -> list[OcrLine]:
        bgr = np.ascontiguousarray(image[..., :3])
        found, _ = self._ocr(bgr)
        out = []
        for box, text, _score in found or []:
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            out.append(
                OcrLine(text, (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)), max(1, len(text.split())))
            )
        return out


def engine() -> Engine:
    """The best OCR here: Windows', else RapidOCR; RuntimeError saying why neither works."""
    errors = []
    for make in (WindowsOcr, RapidOcr):
        try:
            return make()
        except Exception as error:  # noqa: BLE001 - not installed, or no language
            errors.append(f"{make.name}: {type(error).__name__}: {error}")
    raise RuntimeError("; ".join(errors))


# -- Windows ------------------------------------------------------------------------------------------


class Win32System:
    """Windows, through user32, kernel32 and dwmapi (ctypes), and mss for the pixels."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("reading the screen needs Windows")
        import ctypes
        from ctypes import wintypes as w

        import mss

        self.ctypes, self.w, self._mss_module = ctypes, w, mss
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            dwmapi = ctypes.WinDLL("dwmapi")
            dwmapi.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, w.LPVOID, w.DWORD]
            dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        except OSError:
            dwmapi = None
        # Physical pixels everywhere, as the tracker's monitor and mss's captures are.
        try:
            user32.SetProcessDpiAwarenessContext.argtypes = [w.HANDLE]
            user32.SetProcessDpiAwarenessContext(w.HANDLE(-4))  # PER_MONITOR_AWARE_V2; fails if already set
        except AttributeError:
            pass
        user32.GetTopWindow.argtypes = [w.HWND]
        user32.GetTopWindow.restype = w.HWND
        user32.GetWindow.argtypes = [w.HWND, w.UINT]
        user32.GetWindow.restype = w.HWND
        user32.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.GetLayeredWindowAttributes.argtypes = [
            w.HWND,
            ctypes.POINTER(w.DWORD),
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(w.DWORD),
        ]
        user32.GetForegroundWindow.restype = w.HWND
        user32.GetWindowTextLengthW.argtypes = [w.HWND]
        user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        user32.GetWindowThreadProcessId.restype = w.DWORD
        user32.IsIconic.argtypes = [w.HWND]
        user32.IsWindowVisible.argtypes = [w.HWND]
        user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
        user32.GetShellWindow.restype = w.HWND
        user32.GetDesktopWindow.restype = w.HWND
        user32.MonitorFromPoint.argtypes = [w.POINT, w.DWORD]
        user32.MonitorFromPoint.restype = w.HANDLE
        kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel32.OpenProcess.restype = w.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        kernel32.CloseHandle.argtypes = [w.HANDLE]
        kernel32.GetTickCount.restype = w.DWORD

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD)]

        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", w.UINT), ("dwTime", w.DWORD)]

        user32.GetMonitorInfoW.argtypes = [w.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInput)]
        self.MonitorInfo, self.LastInput = MonitorInfo, LastInput
        self.user32, self.kernel32, self.dwmapi = user32, kernel32, dwmapi
        self._mss: Any = None
        self._mss_thread: int | None = None
        self._apps: dict[int, str] = {}  # pid -> program, cached

    def monitor(self) -> tuple[int, int, int, int]:
        """The primary monitor, the one the eye tracker is calibrated on."""
        info = self.MonitorInfo(cbSize=self.ctypes.sizeof(self.MonitorInfo))
        handle = self.user32.MonitorFromPoint(self.w.POINT(0, 0), 1)  # MONITOR_DEFAULTTOPRIMARY
        self.user32.GetMonitorInfoW(handle, self.ctypes.byref(info))
        r = info.rcMonitor
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def window_at(self, x: int, y: int) -> Window | None:
        """The top-level window shown at screen pixel (x, y): the first in z-order, from the top,
        that is visible there."""
        hwnd = self.user32.GetTopWindow(None)
        for _ in range(5000):
            if not hwnd:
                return None
            if self._shown(hwnd):
                rect = self._frame(hwnd)
                if rect.left <= x < rect.right and rect.top <= y < rect.bottom:
                    return self.describe(hwnd)
            hwnd = self.user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT: the next one down
        return None

    def _shown(self, hwnd: Any) -> bool:
        """Visible on this desktop and not see-through: not hidden, minimized, cloaked (another
        virtual desktop, a suspended app), a click-through overlay or fully transparent."""
        ctypes, w, user32 = self.ctypes, self.w, self.user32
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return False
        extended = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF  # GWL_EXSTYLE
        if extended & 0x80000:  # WS_EX_LAYERED
            if extended & 0x20:  # WS_EX_TRANSPARENT: clicks and eyes go through it (the gaze ring)
                return False
            alpha, flags = ctypes.c_ubyte(255), w.DWORD(0)
            got = user32.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(alpha), ctypes.byref(flags))
            if got and flags.value & 2 and alpha.value == 0:  # LWA_ALPHA, fully transparent
                return False
        return not self._cloaked(hwnd)

    def _cloaked(self, hwnd: Any) -> bool:
        if self.dwmapi is None:
            return False
        cloaked = self.w.DWORD()
        size = self.ctypes.sizeof(cloaked)
        return self.dwmapi.DwmGetWindowAttribute(hwnd, 14, self.ctypes.byref(cloaked), size) == 0 and bool(
            cloaked.value
        )

    def _frame(self, hwnd: Any) -> Any:
        """The visible frame, without the invisible resize borders of Windows 10 and 11."""
        rect = self.w.RECT()
        size = self.ctypes.sizeof(rect)
        if self.dwmapi is None or self.dwmapi.DwmGetWindowAttribute(hwnd, 9, self.ctypes.byref(rect), size) != 0:
            self.user32.GetWindowRect(hwnd, self.ctypes.byref(rect))
        return rect

    def foreground(self) -> Window | None:
        return self.describe(self.user32.GetForegroundWindow())

    def idle_s(self) -> float:
        info = self.LastInput(cbSize=self.ctypes.sizeof(self.LastInput))
        if not self.user32.GetLastInputInfo(self.ctypes.byref(info)):
            return 0.0
        return ((self.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000

    def describe(self, hwnd: Any) -> Window | None:
        ctypes, w, user32 = self.ctypes, self.w, self.user32
        if not hwnd or hwnd in (user32.GetShellWindow(), user32.GetDesktopWindow()):
            return None
        if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd) or self._cloaked(hwnd):
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = self._frame(hwnd)
        handle = ctypes.cast(hwnd, ctypes.c_void_p).value or 0
        box = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        return Window(handle, self.app(pid.value), title.value, box, cls.value)

    def app(self, pid: int) -> str:
        if pid in self._apps:
            return self._apps[pid]
        name = "unknown.exe"
        process = self.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if process:
            try:
                size = self.w.DWORD(1024)
                path = self.ctypes.create_unicode_buffer(size.value)
                if self.kernel32.QueryFullProcessImageNameW(process, 0, path, self.ctypes.byref(size)):
                    name = path.value.replace("/", "\\").rsplit("\\", 1)[-1].casefold()
            finally:
                self.kernel32.CloseHandle(process)
        if len(self._apps) > 500:
            self._apps.clear()
        self._apps[pid] = name
        return name

    def _covering(self, rect: tuple[int, int, int, int], window: Window) -> list[tuple[int, int, int, int]]:
        """Rectangles above this window, clipped to the image (left, top, right, bottom). Refuse
        a stale selection rather than reading whatever now occupies its old rectangle."""
        if self.describe(window.hwnd) != window:
            raise OSError("selected window changed before capture completed")
        covers = []
        hwnd = self.user32.GetTopWindow(None)
        for _ in range(5000):
            if not hwnd:
                break
            if hwnd == window.hwnd:
                return covers
            if self._shown(hwnd):
                frame = self._frame(hwnd)
                left, top = max(rect[0], frame.left), max(rect[1], frame.top)
                right = min(rect[0] + rect[2], frame.right)
                bottom = min(rect[1] + rect[3], frame.bottom)
                if left < right and top < bottom:
                    covers.append((left - rect[0], top - rect[1], right - rect[0], bottom - rect[1]))
            hwnd = self.user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT
        raise OSError("selected window changed before capture completed")

    def capture(self, rect: tuple[int, int, int, int], window: Window) -> np.ndarray:
        """Visible pixels belonging to the selected window, BGRA. Whiten covering windows before
        OCR, including browsers and private apps; their text must not bypass their exclusions.
        Check both sides of the screenshot to cover a window moving during the grab."""
        covers = self._covering(rect, window)
        if self._mss is None or self._mss_thread != threading.get_ident():
            self._mss = self._mss_module.mss()
            self._mss_thread = threading.get_ident()
        shot = self._mss.grab({"left": rect[0], "top": rect[1], "width": rect[2], "height": rect[3]})
        covers += self._covering(rect, window)
        image = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4).copy()
        for left, top, right, bottom in covers:
            image[top:bottom, left:right] = 255
        return image

    def close(self) -> None:
        context, self._mss = self._mss, None
        self._mss_thread = None
        if context is not None:
            context.close()


def start(receiver: Any, settings_file: Path) -> tuple[ScreenReader | None, str | None]:
    """The reader, running, or None and why not (not Windows). Without OCR it stops at once and
    says why in its state."""
    try:
        system = Win32System()
    except Exception as error:  # noqa: BLE001
        return None, f"{type(error).__name__}: {error}"
    reader = ScreenReader(receiver, system, engine, settings_file)
    reader.start()
    return reader, None

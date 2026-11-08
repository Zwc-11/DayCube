"""What was on screen through a day, page by page (``GET /day/{date}/seen``, the viewer's What I
saw): when each page or app was in front, and what was read on it line by line, stared at, looked
at, copied, clicked, played and searched from it. Read from the vault database (``store/db.py``),
so it is the same as the search and the day's note."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

from .store import LifeStore
from .time import instant
from .vault import Record

ON_SCREEN = ("web.visit", "screen.app")
DONE = {"web.read": "read", "web.dwell": "stay", "web.look": "look", "web.selection": "copy", "web.click": "click",
        "web.media": "media", "web.search": "search"}  # fmt: skip


def _words(kind: str, text: str, title: str | None) -> str:
    """A unit's own words: past its "Read in <page>: " prefix (the page's title may hold a colon),
    or, for a click or media, before its " in <page>" ending."""
    at = text.find(f" in {title}") if title else -1
    if kind in ("click", "media"):
        return text[:at] if at > 0 else text
    start = text.find(": ", at + len(title) + 4 if at >= 0 and title else 0)
    return text[start + 2 :] if start >= 0 else text


def _site(url: str) -> str:
    if url.startswith("app://"):
        return url.removeprefix("app://").split("/")[0]
    return (urlsplit(url).hostname or "").removeprefix("www.")


def seen(store: LifeStore, day: str) -> Record:
    """The day's pages in the order they first came to the front, each with its spans on screen and
    what was done on it; a search goes with the page in front when it was made."""
    store.ensure([day])
    types = (*ON_SCREEN, *DONE)
    with store.read() as db:
        rows = db.execute(
            "SELECT u.t, u.t_end, u.type, u.text, u.attention, e.key, e.name FROM units u "
            "LEFT JOIN entities e ON e.id = (SELECT m.entity_id FROM mentions m JOIN entities p ON p.id = m.entity_id "
            "WHERE m.unit_id = u.id AND m.role = 'on' AND p.kind = 'page' LIMIT 1) "
            f"WHERE u.day = ? AND u.type IN ({','.join('?' for _ in types)}) "
            "ORDER BY u.ts, u.type IN (?, ?) DESC",  # at one instant, the page comes to the front first
            (day, *types, *ON_SCREEN),
        ).fetchall()
    pages: dict[str, Record] = {}
    front: Record | None = None  # the page last brought to the front
    for t, t_end, kind, text, attention, key, title in rows:
        page = pages.get(key) if key else None
        if key and page is None:
            page = pages[key] = {
                "key": key,
                "title": title,
                "site": _site(key),
                "spans": [],
                "seconds": 0.0,
                "items": [],
            }
        if kind in ON_SCREEN:
            page["spans"].append([t, t_end or t])
            page["seconds"] += (instant(t_end) - instant(t)).total_seconds() if t_end else 0.0
            front = page
            continue
        target = page or (front if front and _in_front(front, t) else None)
        if target is not None:
            target["items"].append(
                {"t": t, "kind": DONE[kind], "text": _words(DONE[kind], text, title), "value": attention}
            )
    listed = sorted((p for p in pages.values() if p["spans"] or p["items"]), key=_first)
    for page in listed:
        page["seconds"] = round(page["seconds"], 1)
    return {
        "date": day,
        "pages": listed,
        "totals": {
            "seconds": round(sum(p["seconds"] for p in listed), 1),
            "pages": len(listed),
            "read": sum(i["kind"] == "read" for p in listed for i in p["items"]),
        },
    }


def _in_front(page: Record, t: str) -> bool:
    start, end = page["spans"][-1]
    return instant(start) <= instant(t) <= instant(end)


def _first(page: Record) -> str:
    times: Iterable[str] = [s[0] for s in page["spans"]] + [i["t"] for i in page["items"]]
    return min(times, key=lambda value: instant(value))

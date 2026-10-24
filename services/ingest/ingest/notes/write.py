"""Linked notes in an Obsidian-compatible folder (``vault/notes``), written from the vault database.

- ``Daily/<date>.md``: what was on screen, read, stared at, searched, copied, and what is due,
  each line linking to the note of the page or course it concerns.
- ``Sources/<page title>.md``: one per page that held attention (read, stared at, looked at, copied
  from, reached from a search, or two minutes or more on screen), with everything done on it
  across days.
- ``Courses/<code>.md``: deadlines, and the pages that name the course.

Every note says which entity it is in its front matter (``daycube: page <url>``), so a note keeps
its file when a page's title changes and a renamed note is still found. Only the generated
region is ever rewritten; the rest of the note is the person's. Times are local to the day.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..store import LifeStore
from ..time import instant
from ..vault import Record, atomic_text
from .markdown import file_name, front_matter, merge, wikilink

ATTENDED = ("web.read", "web.dwell", "web.look", "web.selection")
SOURCE_MIN_S = 120.0  # on screen this long earns a page its own note
LIST_LIMIT = 60  # entries per section of a source note


def _local(t: str, zone: str) -> datetime:
    return instant(t).astimezone(ZoneInfo(zone))


def _quote(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _after_colon(text: str) -> str:
    """A unit's text past its "Read in <page>: " prefix: the words themselves."""
    head, sep, tail = text.partition(": ")
    return tail if sep else text


def _measure(kind: str, attention: float | None) -> str:
    """What a unit's attention number means: line coverage for a read, seconds for a stay or look."""
    if attention is None:
        return ""
    if kind == "web.read":
        return f" ({attention:.0%} of the lines)"
    if kind in ("web.dwell", "web.look"):
        return f" ({attention:.0f} s)"
    return ""


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.0f} min" if seconds >= 60 else f"{seconds:.0f} s"


def _app_of(text: str) -> str:
    """The app a ``screen.app`` unit names: "Used Visual Studio Code for 5 min: main.py" -> "Visual Studio Code"."""
    head = text.split(": ", 1)[0].removeprefix("Used ")
    return head.rsplit(" for ", 1)[0]


class Notes:
    def __init__(self, store: LifeStore, root: Path | None = None) -> None:
        self.store = store
        self.root = root or store.vault.root / "notes"
        self._paths: dict[str, Path] | None = None
        self._taken: set[str] = set()

    # -- which file is which entity --------------------------------------------------------

    def paths(self) -> dict[str, Path]:
        """``"<kind> <key>"`` -> note file, from the notes' own front matter. For the vault's own
        notes folder the database already holds it (``store.sync_notes`` reads only changed notes);
        another folder is read in full."""
        if self._paths is None:
            self._paths = {}
            if self.root == self.store.notes_root:
                self.store.sync_notes()
                with self.store.connect() as db:
                    rows = db.execute("SELECT identity, path FROM notes WHERE identity IS NOT NULL ORDER BY path")
                    for identity, path in rows:
                        self._paths.setdefault(identity, self.root / path)
            elif self.root.is_dir():
                for path in sorted(self.root.rglob("*.md")):
                    try:
                        identity = front_matter(path.read_text(encoding="utf-8")).get("daycube")
                    except (OSError, UnicodeDecodeError):
                        continue
                    if identity:
                        self._paths.setdefault(identity, path)
            self._taken = {p.as_posix().casefold() for p in self._paths.values()}
        return self._paths

    def path_for(self, kind: str, key: str, folder: str, title: str) -> Path:
        identity = f"{kind} {key}"
        found = self.paths().get(identity)
        if found is not None:
            return found
        path = self.root / folder / f"{file_name(title)}.md"
        counter = 2
        # Case-folded: on Windows (and macOS) "Hash Table.md" and "Hash table.md" are one file.
        while path.as_posix().casefold() in self._taken:
            path = self.root / folder / f"{file_name(title)} {counter}.md"
            counter += 1
        self.paths()[identity] = path
        self._taken.add(path.as_posix().casefold())
        return path

    def link(self, path: Path, label: str | None = None) -> str:
        return wikilink(path.relative_to(self.root).as_posix(), label)

    def _write(self, path: Path, generated: str, front: dict[str, str]) -> Path:
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        text = merge(existing, generated, front)
        if text != existing:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_text(path, text)
        return path

    # -- writing ---------------------------------------------------------------------------

    def write_day(self, day: str) -> list[Path]:
        """The daily note of ``day``, and the source and course notes of what it touched."""
        self.store.ensure([day])
        with self.store.connect() as db:
            zone_row = db.execute("SELECT tz FROM days WHERE day = ?", (day,)).fetchone()
            if zone_row is None:
                raise FileNotFoundError(f"No vault day or capture for {day}")
            zone = zone_row[0]
            units = [dict(r) for r in db.execute(
                "SELECT u.*, (SELECT m.entity_id FROM mentions m JOIN entities e ON e.id = m.entity_id "
                "WHERE m.unit_id = u.id AND m.role = 'on' AND e.kind = 'page') AS page_id "
                "FROM units u WHERE u.day = ? ORDER BY u.ts", (day,))]  # fmt: skip
            pages = self._pages(db, {u["page_id"] for u in units if u["page_id"]})
            courses = self._entities(db, "course", [u["id"] for u in units])
            deadlines = self._deadlines(db, day)
            searches = self._searches(db, day)
            written = [self._source(db, page) for page in pages.values() if page["noteworthy"]]
            written += [self._course(db, course) for course in courses.values()]
            written.append(self._daily(day, zone, units, pages, deadlines, searches))
        return written

    def _pages(self, db: sqlite3.Connection, ids: set[int]) -> dict[int, Record]:
        found: dict[int, Record] = {}
        ordered = list(ids)
        for start in range(0, len(ordered), 500):
            chunk = ordered[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            rows = db.execute(f"SELECT id, key, name FROM entities WHERE id IN ({marks})", chunk)
            for row in rows:
                found[row["id"]] = {
                    "id": row["id"],
                    "url": row["key"],
                    "title": row["name"],
                    "seconds": 0.0,
                    "noteworthy": False,
                }
            for page_id, seconds, attended in db.execute(
                "SELECT m.entity_id, "
                "sum(CASE WHEN u.type IN ('web.visit', 'screen.app') "
                "THEN coalesce(julianday(u.t_end) - julianday(u.t), 0) * 86400 "
                "ELSE 0 END), sum(u.type IN ('web.read', 'web.dwell', 'web.look', 'web.selection')) "
                f"FROM mentions m JOIN units u ON u.id = m.unit_id WHERE m.entity_id IN ({marks}) GROUP BY m.entity_id",
                chunk,
            ):
                if page_id in found:
                    found[page_id]["seconds"] = seconds or 0.0
                    found[page_id]["noteworthy"] = bool(attended) or (seconds or 0.0) >= SOURCE_MIN_S
            for (page_id,) in db.execute(
                "SELECT DISTINCT l.dst FROM links l JOIN entities q ON q.id = l.src "
                f"WHERE l.dst IN ({marks}) AND l.kind = 'led_to' AND q.kind = 'query'",
                chunk,
            ):
                if page_id in found:
                    found[page_id]["noteworthy"] = True
        # Keep the caller's order: it also determines the order in which notes are written.
        return {page_id: found[page_id] for page_id in ordered if page_id in found}

    def _entities(self, db: sqlite3.Connection, kind: str, unit_ids: list[int]) -> dict[int, Record]:
        if not unit_ids:
            return {}
        out = {}
        for start in range(0, len(unit_ids), 500):
            chunk = unit_ids[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            for row in db.execute(
                f"SELECT DISTINCT e.id, e.key, e.name FROM mentions m JOIN entities e ON e.id = m.entity_id "
                f"WHERE e.kind = ? AND m.unit_id IN ({marks})",
                (kind, *chunk),
            ):
                out[row["id"]] = dict(row)
        return out

    def _deadlines(self, db: sqlite3.Connection, day: str) -> list[Record]:
        """Deadlines seen on ``day``: (deadline, course or page, the record that showed it)."""
        rows = db.execute(
            "SELECT d.key AS deadline_key, d.name AS deadline, s.kind AS owner_kind, s.key AS owner_key, "
            "s.name AS owner, s.id AS owner_id, l.evidence_id, u.t, u.text "
            "FROM links l JOIN entities d ON d.id = l.dst JOIN entities s ON s.id = l.src "
            "LEFT JOIN units u ON u.day = l.day AND u.evidence_id = l.evidence_id "
            "WHERE l.day = ? AND l.kind = 'due' ORDER BY d.key",
            (day,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _searches(self, db: sqlite3.Connection, day: str) -> dict[str, list[int]]:
        """Search evidence id -> the pages it led to."""
        out: dict[str, list[int]] = defaultdict(list)
        for row in db.execute(
            "SELECT l.evidence_id, l.dst FROM links l JOIN entities q ON q.id = l.src "
            "WHERE l.day = ? AND l.kind = 'led_to' AND q.kind = 'query'",
            (day,),
        ):
            out[row["evidence_id"]].append(row["dst"])
        return out

    def _source_path(self, page: Record) -> Path:
        return self.path_for("page", page["url"], "Sources", page["title"])

    def _course_path(self, course: Record) -> Path:
        return self.path_for("course", course["key"], "Courses", course["name"])

    def _source(self, db: sqlite3.Connection, page: Record) -> Path:
        path = self._source_path(page)
        rows = [dict(r) for r in db.execute(
            "SELECT u.day, u.t, u.t_end, u.type, u.text, u.attention, d.tz FROM mentions m JOIN units u ON u.id = m.unit_id "
            "JOIN days d ON d.day = u.day WHERE m.entity_id = ? ORDER BY u.ts", (page["id"],))]  # fmt: skip
        site = page["url"].split("/")[2].removeprefix("www.") if "://" in page["url"] else ""
        seconds = sum(
            (instant(r["t_end"]) - instant(r["t"])).total_seconds()
            for r in rows
            if r["type"] in ("web.visit", "screen.app") and r["t_end"]
        )
        days = sorted({r["day"] for r in rows})
        lines = [
            f"**Site:** {site} · **Seen:** {days[0] if days else '—'}"
            + (f" to {days[-1]}" if len(days) > 1 else "")
            + f" · **On screen:** {_minutes(seconds)}",
            "",
        ]
        sections = {"web.read": "Read", "web.dwell": "Stayed on", "web.look": "Looked at", "web.selection": "Copied"}
        for kind, heading in sections.items():
            entries = [r for r in rows if r["type"] == kind][-LIST_LIMIT:]
            if not entries:
                continue
            lines.append(f"## {heading}")
            for r in entries:
                when = _local(r["t"], r["tz"]).strftime("%Y-%m-%d %H:%M")
                lines.append(f"- {when}: {_quote(_after_colon(r['text']))}{_measure(kind, r['attention'])}")
            lines.append("")
        queries = db.execute(
            "SELECT DISTINCT q.name, l.day FROM links l JOIN entities q ON q.id = l.src "
            "WHERE l.dst = ? AND l.kind = 'led_to' AND q.kind = 'query' ORDER BY l.day",
            (page["id"],),
        ).fetchall()
        if queries:
            lines.append("## Found by searching")
            lines += [f"- “{q['name']}” ({q['day']})" for q in queries]
            lines.append("")
        courses = db.execute(
            "SELECT DISTINCT c.id, c.key, c.name FROM links l JOIN entities c ON c.id = l.dst "
            "WHERE l.src = ? AND l.kind = 'mentions' AND c.kind = 'course'",
            (page["id"],),
        ).fetchall()
        if courses:
            lines.append("## Courses")
            lines += [f"- {self.link(self._course_path(dict(c)))}" for c in courses]
            lines.append("")
        front = {"daycube": f"page {page['url']}", "url": page["url"], "site": site, "tags": "[source]"}
        return self._write(path, "\n".join(lines), front)

    def _course(self, db: sqlite3.Connection, course: Record) -> Path:
        path = self._course_path(course)
        deadlines = db.execute(
            "SELECT DISTINCT d.key, d.name, l.day FROM links l JOIN entities d ON d.id = l.dst "
            "WHERE l.src = ? AND l.kind = 'due' ORDER BY d.key",
            (course["id"],),
        ).fetchall()
        pages = db.execute(
            "SELECT DISTINCT p.id, p.key, p.name FROM links l JOIN entities p ON p.id = l.src "
            "WHERE l.dst = ? AND l.kind = 'mentions' AND p.kind = 'page' ORDER BY p.name",
            (course["id"],),
        ).fetchall()
        lines = []
        if deadlines:
            lines.append("## Due")
            for d in deadlines:
                when = d["key"].split("|")[0].replace("T", " ")
                lines.append(f"- {when} (seen {d['day']})")
            lines.append("")
        if pages:
            lines.append("## Pages")
            for p in pages:
                page = {"url": p["key"], "title": p["name"], "id": p["id"]}
                known = self.paths().get(f"page {p['key']}")
                lines.append(f"- {self.link(known) if known else p['name']}")
                del page
            lines.append("")
        front = {"daycube": f"course {course['key']}", "tags": "[course]"}
        return self._write(path, "\n".join(lines) or "Nothing recorded yet.", front)

    def _daily(
        self,
        day: str,
        zone: str,
        units: list[Record],
        pages: dict[int, Record],
        deadlines: list[Record],
        searches: dict[str, list[int]],
    ) -> Path:
        path = self.path_for("day", day, "Daily", day)

        def page_link(page_id: int | None, fallback: str) -> str:
            page = pages.get(page_id) if page_id else None
            if page and page["noteworthy"]:
                return self.link(self._source_path(page), _quote(page["title"], 80))
            return _quote(page["title"] if page else fallback, 80)

        def hhmm(t: str) -> str:
            return _local(t, zone).strftime("%H:%M")

        lines: list[str] = []
        visits = [u for u in units if u["type"] == "web.visit"]
        if visits:
            total: dict[int | None, float] = defaultdict(float)
            for u in visits:
                total[u["page_id"]] += (instant(u["t_end"]) - instant(u["t"])).total_seconds() if u["t_end"] else 0
            lines.append(f"## On screen ({_minutes(sum(total.values()))})")
            for u in visits:
                seconds = (instant(u["t_end"]) - instant(u["t"])).total_seconds() if u["t_end"] else 0
                lines.append(f"- {hhmm(u['t'])} {page_link(u['page_id'], u['text'])} · {_minutes(seconds)}")
            lines.append("")
        apps = [u for u in units if u["type"] == "screen.app"]
        if apps:
            # Each app with its time, and its windows by time (a window with a note links to it).
            by_app: dict[str, list[Record]] = defaultdict(list)
            for u in apps:
                by_app[_app_of(u["text"])].append(u)

            def spent(group: list[Record]) -> float:
                return sum((instant(u["t_end"]) - instant(u["t"])).total_seconds() for u in group if u["t_end"])

            lines.append(f"## Apps ({_minutes(spent(apps))})")
            for name, group in sorted(by_app.items(), key=lambda item: -spent(item[1])):
                windows: dict[str, list[Record]] = defaultdict(list)
                for u in group:
                    if ": " in u["text"]:
                        windows[u["text"].split(": ", 1)[1]].append(u)
                top = sorted(windows.items(), key=lambda item: -spent(item[1]))[:5]
                shown = ", ".join(page_link(g[0]["page_id"], title) for title, g in top)
                lines.append(f"- {name} · {_minutes(spent(group))}" + (f": {shown}" if shown else ""))
            lines.append("")
        sections = (
            ("web.read", "Read"),
            ("web.dwell", "Stayed on"),
            ("web.look", "Looked at"),
            ("web.selection", "Copied"),
        )
        for kind, heading in sections:
            entries = [u for u in units if u["type"] == kind]
            if not entries:
                continue
            lines.append(f"## {heading}")
            for u in entries:
                measure = _measure(kind, u["attention"])
                lines.append(
                    f"- {hhmm(u['t'])} {page_link(u['page_id'], '')}: {_quote(_after_colon(u['text']))}{measure}"
                )
            lines.append("")
        found = [u for u in units if u["type"] == "web.search"]
        if found:
            lines.append("## Searched")
            for u in found:
                led = [page_link(p, "") for p in searches.get(u["evidence_id"], [])]
                query = _after_colon(u["text"])
                lines.append(f"- {hhmm(u['t'])} “{query}”" + (f" → {', '.join(led)}" if led else ""))
            lines.append("")
        if deadlines:
            lines.append("## Due")
            seen: set[str] = set()
            for d in deadlines:
                if d["deadline_key"] in seen:
                    continue
                seen.add(d["deadline_key"])
                when = d["deadline_key"].split("|")[0].replace("T", " ")
                if d["owner_kind"] == "course":
                    owner = self.link(self._course_path({"key": d["owner_key"], "name": d["owner"]}))
                else:
                    owner = page_link(d["owner_id"], d["owner"])
                at = f" (seen {hhmm(d['t'])})" if d["t"] else ""
                lines.append(f"- {owner} due {when}{at}")
            lines.append("")
        places = [u for u in units if u["type"] == "place.visit"]
        if places:
            lines.append("## Places")
            lines += [f"- {hhmm(u['t'])} {u['text'].removeprefix('Visited ')}" for u in places]
            lines.append("")
        front = {"daycube": f"day {day}", "date": day, "tags": "[daily]"}
        return self._write(path, "\n".join(lines) or "Nothing recorded.", front)

"""The notes as the viewer reads them: a day's knowledge base, one note with its links and
backlinks, and which note each search result belongs to.

A day's notes are written on first read and again whenever the day's records changed since
(``DayNotes``), so a day opened in the viewer is never older than its capture logs.
"""

from __future__ import annotations

import threading
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..cache import SegmentedLRU
from ..store import LifeStore
from ..vault import METADATA_CACHE_BYTES, Record
from .write import Notes

KINDS = {"day": "daily", "page": "source", "course": "course"}


def note_kind(identity: str | None) -> str:
    return KINDS.get((identity or "").split(" ", 1)[0], "note")


class DayNotes:
    """Keeps each day's notes written for the records the database holds for it."""

    def __init__(self, store: LifeStore) -> None:
        self.store = store
        self._written: SegmentedLRU[str, str] = SegmentedLRU(METADATA_CACHE_BYTES)
        self._lock = threading.Lock()

    def ensure(self, day: str) -> None:
        self.store.ensure([day])
        with self.store.connect() as db:
            row = db.execute("SELECT stamp FROM days WHERE day = ?", (day,)).fetchone()
            daily = db.execute("SELECT path FROM notes WHERE identity = ?", (f"day {day}",)).fetchone()
        if row is None:
            raise FileNotFoundError(f"Nothing recorded on {day}")
        with self._lock:
            if self._written.get(day) != row[0] or daily is None:
                Notes(self.store).write_day(day)
                self._written.put_metadata(day, row[0])
        self.store.sync_notes()  # the person's own notes may have changed too

    def view(self, day: str) -> Record:
        """The day's knowledge base: its daily note, the notes it links to, the notes that link to
        it, its local graph (each node with the note it has, if any) and what it holds."""
        self.ensure(day)
        with self.store.connect() as db:
            daily_row = db.execute("SELECT path FROM notes WHERE identity = ?", (f"day {day}",)).fetchone()
            daily = daily_row[0] if daily_row else None
            out = [r[0] for r in db.execute(
                "SELECT resolved FROM note_links WHERE path = ? AND resolved IS NOT NULL GROUP BY resolved "
                "ORDER BY min(rowid)", (daily,))] if daily else []  # fmt: skip
            back = [r[0] for r in db.execute(
                "SELECT path FROM note_links WHERE resolved = ? AND path != ? GROUP BY path ORDER BY path",
                (daily, daily))] if daily else []  # fmt: skip
            row = db.execute("SELECT tz, units FROM days WHERE day = ?", (day,)).fetchone()
            zone, units = row["tz"], row["units"]
            kinds = dict(
                db.execute("SELECT type, count(*) FROM units WHERE day = ? GROUP BY type ORDER BY 2 DESC", (day,))
            )
        start = datetime.combine(datetime.fromisoformat(day).date(), time(), ZoneInfo(zone))
        graph = self.store.graph(since=start.timestamp(), until=(start + timedelta(days=1)).timestamp(), limit=150)
        # Only fetch notes this view can display. Loading the entire folder here made every
        # day view allocate several megabytes for unrelated notes.
        notes: dict[str, Record] = {}
        paths = list(dict.fromkeys(([daily] if daily else []) + out + back))
        identities = [f"{node['kind']} {node['key']}" for node in graph["nodes"]]
        by_identity: dict[str, str] = {}
        with self.store.connect() as db:
            for start in range(0, len(paths), 500):
                chunk = paths[start : start + 500]
                marks = ",".join("?" for _ in chunk)
                notes.update(
                    (r["path"], dict(r))
                    for r in db.execute(f"SELECT path, title, identity FROM notes WHERE path IN ({marks})", chunk)
                )
            for start in range(0, len(identities), 500):
                chunk = identities[start : start + 500]
                marks = ",".join("?" for _ in chunk)
                # Match the previous table-scan order when multiple notes have one identity.
                by_identity.update(
                    db.execute(f"SELECT identity, path FROM notes WHERE identity IN ({marks}) ORDER BY rowid", chunk)
                )
        for node in graph["nodes"]:
            node["note"] = by_identity.get(f"{node['kind']} {node['key']}")

        def entry(path: str, role: str) -> Record:
            note = notes[path]
            return {"path": path, "title": note["title"], "kind": note_kind(note["identity"]), "role": role}

        listed = [entry(daily, "daily")] if daily in notes else []
        listed += [entry(p, "linked") for p in out if p in notes and p != daily]
        listed += [entry(p, "backlink") for p in back if p in notes and p not in out]
        return {
            "date": day,
            "daily": daily,
            "notes": listed,
            "graph": graph,
            "records": units,
            "kinds": kinds,
            "places": self.store.has_day(day) and kinds.get("place.visit", 0) > 0,
        }


def note_path(root: Path, relative: str) -> Path:
    """A note inside the notes folder, or ValueError: no absolute paths, no ``..``, no hidden
    folders, Markdown only."""
    if not relative or relative.startswith(("/", "\\")) or "\\" in relative:
        raise ValueError("Give a note path relative to the notes folder")
    parts = relative.split("/")
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts) or not relative.endswith(".md"):
        raise ValueError("Not a note path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Not a note path")
    if not path.is_file():
        raise FileNotFoundError(relative)
    return path


def read_note(store: LifeStore, relative: str) -> Record:
    """One note: its Markdown as written, each link with the note it opens (None: not written
    yet), and the notes that link to it."""
    path = note_path(store.notes_root, relative)
    stat = path.stat()
    text = path.read_text(encoding="utf-8")
    with store.connect() as db:
        known = db.execute("SELECT mtime_ns, size FROM notes WHERE path = ?", (relative,)).fetchone()
    if known is None or tuple(known) != (stat.st_mtime_ns, stat.st_size):
        store.sync_notes()  # this note changed since the folder was read (its links may have too)
    with store.connect() as db:
        row = db.execute("SELECT title, identity FROM notes WHERE path = ?", (relative,)).fetchone()
        links = [dict(r) for r in db.execute(
            "SELECT target, label, resolved AS path FROM note_links WHERE path = ? ORDER BY rowid", (relative,))]  # fmt: skip
        backlinks = [dict(r) for r in db.execute(
            "SELECT DISTINCT n.path, n.title FROM note_links l JOIN notes n ON n.path = l.path "
            "WHERE l.resolved = ? AND l.path != ? ORDER BY n.path", (relative, relative))]  # fmt: skip
    identity = row["identity"] if row else None
    return {
        "path": relative,
        "title": row["title"] if row else path.stem,
        "identity": identity,
        "kind": note_kind(identity),
        "markdown": text,
        "links": links,
        "backlinks": backlinks,
        "file": str(path),
        "modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
    }


def attach_notes(store: LifeStore, evidence: list[Record]) -> None:
    """Give each search result the note it is in: the page's source note, else the day's daily
    note; a note hit is its own note. ``match``: the words to find in that note."""
    unit_ids = [e["unit_id"] for e in evidence if e.get("unit_id") is not None]
    pages: dict[int, str] = {}
    with store.connect() as db:
        if unit_ids:
            marks = ",".join("?" for _ in unit_ids)
            for unit_id, key in db.execute(
                # CROSS JOIN keeps this order: from the few results' mentions to their entities, not
                # every page entity probed for the results (4.3 ms to 0.04 ms at 30 days).
                "SELECT m.unit_id, e.key FROM mentions m CROSS JOIN entities e ON e.id = m.entity_id "
                f"WHERE m.unit_id IN ({marks}) AND m.role = 'on' AND e.kind = 'page'",
                unit_ids,
            ):
                pages[unit_id] = key
        wanted = [f"page {key}" for key in set(pages.values())] + [
            f"day {d}" for d in {e.get("day") for e in evidence} if d
        ]
        by_identity: dict[str, str] = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            by_identity |= dict(db.execute(
                f"SELECT identity, path FROM notes WHERE identity IN ({','.join('?' for _ in chunk)})", chunk))  # fmt: skip
    for e in evidence:
        text = " ".join(e["text"].split())
        if e["type"] == "note.text":
            e["note"] = e["source_ref"].removeprefix("notes/")
            e["match"] = text.split(": ", 1)[-1][:60]
            continue
        page = pages.get(e["unit_id"])
        e["note"] = by_identity.get(f"page {page}") if page else None
        if e["note"] is None and e.get("day"):
            e["note"] = by_identity.get(f"day {e['day']}")
        e["match"] = text.split(": ", 1)[-1][:60]

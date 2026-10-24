"""The notes folder read back into the vault database: the person's writing becomes searchable
and their ``[[links]]`` join the knowledge graph.

Only what the person wrote is indexed (``notes.markdown.user_text``); DayCube's generated regions
repeat records the database already holds. A link resolves the way Obsidian resolves it: a path
(``[[Sources/Hash table - Wikipedia]]``) names that file, a bare name the note with that file name.
A note DayCube generated says which entity it is (``daycube: page <url>``), so a link to it
connects to that page, course or day; any other note is an entity of kind ``note``, and a link to a
note that does not exist yet still makes one (Obsidian's unresolved links), so the graph is the
same before and after the note is written. Note links have no day (``day = ''``).
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Container, Iterable
from pathlib import Path

from ..notes.markdown import chunks, front_matter, links, user_text

# Every [[link]] of every note, generated or written, with the note it resolves to (NULL when no
# such note exists yet): outgoing links and backlinks, as Obsidian shows them. ``name``: the
# target's file name, case-folded, to find the links a note added or removed may now open. Added
# without a schema change, so an existing database gains it in place.
LINKS_DDL = """
CREATE TABLE IF NOT EXISTS note_links (
  path TEXT NOT NULL, target TEXT NOT NULL, label TEXT, name TEXT NOT NULL, resolved TEXT
);
CREATE INDEX IF NOT EXISTS note_links_path ON note_links(path);
CREATE INDEX IF NOT EXISTS note_links_name ON note_links(name);
CREATE INDEX IF NOT EXISTS note_links_resolved ON note_links(resolved);
"""

DDL = (
    """
CREATE TABLE notes (path TEXT PRIMARY KEY, title TEXT NOT NULL, identity TEXT, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL);
CREATE TABLE note_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, heading TEXT NOT NULL, text TEXT NOT NULL
);
CREATE INDEX note_chunks_path ON note_chunks(path);
CREATE VIRTUAL TABLE note_chunks_fts USING fts5(text, content='note_chunks', content_rowid='id');
"""
    + LINKS_DDL
)


def scan(root: Path) -> dict[str, tuple[int, int]]:
    """Every note in the folder: relative path -> (mtime_ns, size). ``os.scandir`` rather than
    ``Path.rglob``: on Windows the listing carries each file's times and size, so 5,000 notes cost
    one directory read each instead of a stat per file and pathlib objects (270 ms here)."""
    out: dict[str, tuple[int, int]] = {}

    def walk(folder: str, prefix: str) -> None:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue  # .obsidian, .trash
            try:
                if entry.is_dir(follow_symlinks=False):
                    walk(entry.path, f"{prefix}{entry.name}/")
                elif entry.name.endswith(".md") and entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    out[f"{prefix}{entry.name}"] = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                continue

    if root.is_dir():
        walk(str(root), "")
    return out


def sync(
    db: sqlite3.Connection, root: Path, found: dict[str, tuple[int, int]], entity: Callable[[str, str, str], int]
) -> int:
    """Bring the notes tables and note links in line with the folder; returns notes rewritten.
    ``entity(kind, key, name) -> id`` creates or finds an entity in the caller's transaction."""
    rows = db.execute("SELECT path, mtime_ns, size, identity FROM notes").fetchall()
    known = {row[0]: (row[1], row[2]) for row in rows}
    identities = {row[0]: row[3] for row in rows}
    changed = [path for path, stamp in found.items() if known.get(path) != stamp]
    removed = [path for path in known if path not in found]
    for path in removed:
        _forget(db, path)
    texts: dict[str, str] = {}
    for path in changed:
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        texts[path] = text
        _forget(db, path)
        identity = front_matter(text).get("daycube")
        title = path.rsplit("/", 1)[-1].removesuffix(".md")
        db.execute("INSERT INTO notes VALUES (?, ?, ?, ?, ?)", (path, title, identity, *found[path]))
        db.executemany(
            "INSERT INTO note_chunks(path, heading, text) VALUES (?, ?, ?)",
            [(path, c.heading, c.text) for c in chunks(text)],
        )
        db.execute("INSERT INTO note_chunks_fts(rowid, text) SELECT id, text FROM note_chunks WHERE path = ?", (path,))
    # Which entity each note is, and what its links point at. A note added or removed changes what
    # links to its name open, and a note that now names another entity changes what links to it
    # stand for: those linking notes are read again, and only those.
    added = [path for path in changed if path not in known]
    renamed = [path for path in changed if path in known and path in texts
               and front_matter(texts[path]).get("daycube") != identities.get(path)]  # fmt: skip
    again: set[str] = set()
    affected_names = sorted({link_name(p) for p in [*added, *removed]})
    for start in range(0, len(affected_names), 500):
        chunk = affected_names[start : start + 500]
        marks = ",".join("?" for _ in chunk)
        again.update(r[0] for r in db.execute(f"SELECT DISTINCT path FROM note_links WHERE name IN ({marks})", chunk))
    for path in renamed:
        again.update(r[0] for r in db.execute("SELECT DISTINCT path FROM note_links WHERE resolved = ?", (path,)))
    for path in sorted(again - set(texts)):
        if path in found:
            try:
                texts[path] = (root / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    if texts:
        notes = {row[0]: (row[1], row[2]) for row in db.execute("SELECT path, title, identity FROM notes")}
        by_name = names(notes)

        def node(path: str) -> int:
            title, identity = notes[path]
            if identity and " " in identity:
                kind, key = identity.split(" ", 1)
                return entity(kind, key, title)
            return entity("note", path.removesuffix(".md"), title)

        def resolve(name: str) -> str | None:
            return resolve_link(name, notes, by_name)

        def target(name: str) -> int:
            path = resolve(name)
            if path is not None:
                return node(path)
            wanted = name.removesuffix(".md")
            return entity("note", wanted, wanted.rsplit("/", 1)[-1])

        for path, text in texts.items():
            if path not in notes:
                continue
            db.execute("DELETE FROM links WHERE day = '' AND evidence_id = ?", (path,))
            db.execute("DELETE FROM note_links WHERE path = ?", (path,))
            db.executemany(
                "INSERT INTO note_links VALUES (?, ?, ?, ?, ?)",
                [(path, name, label, link_name(name), resolve(name)) for name, label in links(text)],
            )
            source = node(path)
            # Only the person's links join the graph: a generated region's restate what it holds.
            rows = {(source, target(name)) for name, _ in links(user_text(text))}
            db.executemany(
                "INSERT OR IGNORE INTO links VALUES ('', ?, ?, 'links', ?)",
                [(a, b, path) for a, b in rows if a != b],
            )
    return len(changed) + len(removed)


def link_name(target: str) -> str:
    """The file name a link or note path comes down to, case-folded: ``Sources/Hash Table.md`` and
    ``[[hash table]]`` both give ``hash table``."""
    return target.removesuffix(".md").rsplit("/", 1)[-1].casefold()


def resolve_link(name: str, notes: Container[str], by_name: dict[str, str]) -> str | None:
    """The note a ``[[name]]`` opens, as Obsidian resolves it: a path names that file, a bare name
    the note with that file name (``by_name``: case-folded name -> shortest path). None: no such note."""
    wanted = name.removesuffix(".md")
    if "/" in wanted and f"{wanted}.md" in notes:
        return f"{wanted}.md"
    return by_name.get(wanted.rsplit("/", 1)[-1].casefold())


def names(paths: Iterable[str]) -> dict[str, str]:
    """Case-folded file name -> path, the shortest path winning (Obsidian's rule for bare names)."""
    out: dict[str, str] = {}
    for path in sorted(paths, key=lambda p: (p.count("/"), p)):
        out.setdefault(path.rsplit("/", 1)[-1].removesuffix(".md").casefold(), path)
    return out


def _forget(db: sqlite3.Connection, path: str) -> None:
    db.execute(
        "INSERT INTO note_chunks_fts(note_chunks_fts, rowid, text) SELECT 'delete', id, text FROM note_chunks WHERE path = ?",
        (path,),
    )
    db.execute("DELETE FROM note_chunks WHERE path = ?", (path,))
    db.execute("DELETE FROM notes WHERE path = ?", (path,))
    db.execute("DELETE FROM links WHERE day = '' AND evidence_id = ?", (path,))
    db.execute("DELETE FROM note_links WHERE path = ?", (path,))

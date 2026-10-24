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

import sqlite3
from collections.abc import Callable
from pathlib import Path

from ..notes.markdown import chunks, front_matter, links, user_text

DDL = """
CREATE TABLE notes (path TEXT PRIMARY KEY, title TEXT NOT NULL, identity TEXT, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL);
CREATE TABLE note_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, heading TEXT NOT NULL, text TEXT NOT NULL
);
CREATE INDEX note_chunks_path ON note_chunks(path);
CREATE VIRTUAL TABLE note_chunks_fts USING fts5(text, content='note_chunks', content_rowid='id');
"""


def scan(root: Path) -> dict[str, tuple[int, int]]:
    """Every note in the folder: relative path -> (mtime_ns, size)."""
    if not root.is_dir():
        return {}
    out = {}
    for path in root.rglob("*.md"):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue  # .obsidian, .trash
        try:
            stat = path.stat()
        except OSError:
            continue
        out[path.relative_to(root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return out


def sync(
    db: sqlite3.Connection, root: Path, found: dict[str, tuple[int, int]], entity: Callable[[str, str, str], int]
) -> int:
    """Bring the notes tables and note links in line with the folder; returns notes rewritten.
    ``entity(kind, key, name) -> id`` creates or finds an entity in the caller's transaction."""
    known = {row[0]: (row[1], row[2]) for row in db.execute("SELECT path, mtime_ns, size FROM notes")}
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
    # Which entity each note is, and what its links point at. The set of notes decides how a bare
    # name resolves, so a note added or removed re-resolves every note's links.
    if removed or set(changed) - set(known):
        for (path,) in db.execute("SELECT path FROM notes").fetchall():
            if path not in texts:
                try:
                    texts[path] = (root / path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
    if texts:
        notes = {row[0]: (row[1], row[2]) for row in db.execute("SELECT path, title, identity FROM notes")}
        by_name: dict[str, str] = {}
        for path in sorted(notes, key=lambda p: (p.count("/"), p)):  # Obsidian: the shortest path wins
            by_name.setdefault(notes[path][0].casefold(), path)

        def node(path: str) -> int:
            title, identity = notes[path]
            if identity and " " in identity:
                kind, key = identity.split(" ", 1)
                return entity(kind, key, title)
            return entity("note", path.removesuffix(".md"), title)

        def target(name: str) -> int:
            wanted = name.removesuffix(".md")
            if "/" in wanted and f"{wanted}.md" in notes:
                return node(f"{wanted}.md")
            path = by_name.get(wanted.rsplit("/", 1)[-1].casefold())
            if path is not None:
                return node(path)
            return entity("note", wanted, wanted.rsplit("/", 1)[-1])

        for path, text in texts.items():
            if path not in notes:
                continue
            db.execute("DELETE FROM links WHERE day = '' AND evidence_id = ?", (path,))
            source = node(path)
            # Only the person's links: a generated region's links restate what the graph already holds.
            rows = {(source, target(name)) for name, _ in links(user_text(text))}
            db.executemany(
                "INSERT OR IGNORE INTO links VALUES ('', ?, ?, 'links', ?)",
                [(a, b, path) for a, b in rows if a != b],
            )
    return len(changed) + len(removed)


def _forget(db: sqlite3.Connection, path: str) -> None:
    db.execute(
        "INSERT INTO note_chunks_fts(note_chunks_fts, rowid, text) SELECT 'delete', id, text FROM note_chunks WHERE path = ?",
        (path,),
    )
    db.execute("DELETE FROM note_chunks WHERE path = ?", (path,))
    db.execute("DELETE FROM notes WHERE path = ?", (path,))
    db.execute("DELETE FROM links WHERE day = '' AND evidence_id = ?", (path,))

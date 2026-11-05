"""Every note in vault/notes and every ``[[link]]`` between them: the vault's graph, as Obsidian's
graph view draws it (notes as nodes, links as edges, links to notes not written yet as their own
nodes).

Built from the ``notes`` and ``note_links`` tables the folder is read into, so it costs two queries,
and kept as bytes until a note changes: the same folder is served again with no queries beyond the
stamp, and a client holding it gets an empty 304.
"""

from __future__ import annotations

import hashlib
import json
import threading

from ..store import LifeStore
from .read import DayNotes, note_kind


class NoteGraph:
    def __init__(self, store: LifeStore, day_notes: DayNotes) -> None:
        self.store = store
        self.day_notes = day_notes
        self._lock = threading.Lock()
        self._cached: tuple[str, bytes] | None = None

    def tagged(self) -> tuple[bytes, str]:
        """The graph as JSON bytes and its ETag. Every recorded day has its daily note first."""
        self.day_notes.ensure_all()
        with self.store.connect() as db:
            rows = db.execute("SELECT path, identity, mtime_ns, size FROM notes ORDER BY path").fetchall()
        digest = hashlib.blake2b(digest_size=12)
        for path, identity, mtime_ns, size in rows:
            digest.update(f"{path}\0{identity}\0{mtime_ns}\0{size}\n".encode())
        etag = f'"{digest.hexdigest()}"'
        with self._lock:
            if self._cached is not None and self._cached[0] == etag:
                return self._cached[1], etag
        with self.store.connect() as db:
            links = db.execute("SELECT path, target, resolved FROM note_links").fetchall()
        body = json.dumps(build([(r[0], r[1]) for r in rows], links), separators=(",", ":")).encode()
        with self._lock:
            self._cached = (etag, body)
        return body, etag


def build(notes: list[tuple[str, str | None]], links: list[tuple[str, str, str | None]]) -> dict:
    """``notes``: (path, identity) by path; ``links``: (from, target as written, note it opens or
    None). Edges are [a, b, count]: ``a`` indexes ``notes``, ``b`` too, or ``missing`` after them.
    A link to a note not written yet is one node per target, matched as Obsidian matches names
    (case-insensitive, without ``.md``); a note linking to itself draws nothing."""
    index = {path: i for i, (path, _) in enumerate(notes)}
    missing: dict[str, int] = {}
    names: list[str] = []
    counts: dict[tuple[int, int], int] = {}
    for source, target, resolved in links:
        a = index.get(source)
        if a is None:
            continue
        if resolved is not None:
            b = index.get(resolved)
            if b is None:
                continue
        else:
            name = target.removesuffix(".md").strip()
            if not name:
                continue
            key = name.casefold()
            if key not in missing:
                missing[key] = len(notes) + len(names)
                names.append(name)
            b = missing[key]
        if a != b:
            counts[(a, b)] = counts.get((a, b), 0) + 1
    return {
        "notes": [{"path": path, "kind": note_kind(identity)} for path, identity in notes],
        "missing": names,
        "edges": [[a, b, n] for (a, b), n in sorted(counts.items())],
    }

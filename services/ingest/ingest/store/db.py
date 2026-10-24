"""One database for the whole vault: every searchable unit of every day.

``vault/daycube.sqlite`` holds each unit's row, its keyword index (FTS5) and, when meaning search
is on, its int8 embedding and sign bits. The day folders stay the source of truth: nothing in the
database is written anywhere else, so deleting it costs only the time to rebuild.

A day's rows are replaced in one transaction when its inputs change (compared by source stamp),
so a reader never sees half of a day, and a failed rebuild leaves the previous rows searchable.
Embedding is the slow step, about 30-50 texts a second for bge-small on two CPU cores, so it runs
outside the vault lock, and it only ever embeds text the database has not seen: a re-indexed day
reuses the vectors of its unchanged units, and repeated text on other days ("Visited Home", the
same document on another day) reuses theirs. Nearest-neighbour search is in ``vectors.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

from ..capture import capture_days, capture_stamp, capture_units, read_capture
from ..capture.synthesize import LOGS
from ..graph import Graph, extract
from ..index.build import UNIT_COLUMNS, collect_units, source_stamp
from ..index.embed import Embedder
from ..time import instant
from ..vault import Record, Vault, stored
from . import notes as note_index
from .graph import entity_view, graph_view
from .vectors import VectorIndex, sign_bits

FILE_NAME = "daycube.sqlite"
FULL_CHECK_S = 2.0  # how often a search across days re-checks every day's files

# What is derived from the files (units, graph, note links), separately from the table layout:
# when it changes, every day and note is read again on the next use, keeping the stored vectors
# (they are found again by text). 2: readable deadline names, note links from the person's text only.
# 3: every note link kept with its resolution (outgoing links and backlinks). 4: note links keep
# the target's file name (the table is rebuilt).
DERIVED_VERSION = "4"
SCHEMA_VERSION = 4  # 1: sqlite-vec vectors and per-row triggers, never released; 2: no graph; 3: no notes
# A query may embed this many new texts before it answers (a few seconds on CPU); a day with
# more waits for `daycube index`, and is searched by keywords until then.
INLINE_EMBED_LIMIT = 256
CHUNK = 500  # bound parameters per IN (...) lookup

COLUMNS = (*UNIT_COLUMNS, "ts", "text_hash")
INSERT_UNIT = f"INSERT INTO units({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})"

DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- generation: a vault-wide counter value, new on every write of the day, so in-memory search
-- blocks know to reload. Never reused, even after a day is deleted and indexed again.
CREATE TABLE days (
  day TEXT PRIMARY KEY, tz TEXT NOT NULL, stamp TEXT NOT NULL, units INTEGER NOT NULL,
  model TEXT, unembedded INTEGER NOT NULL, generation INTEGER NOT NULL
);
-- AUTOINCREMENT: ids are never reused, so a vector can never point at another unit.
CREATE TABLE units (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, t TEXT NOT NULL, t_end TEXT,
  type TEXT NOT NULL, text TEXT NOT NULL, place TEXT, floor INTEGER, x REAL, y REAL,
  evidence_id TEXT NOT NULL, source_ref TEXT NOT NULL, attention REAL, fidelity TEXT NOT NULL,
  confidence REAL NOT NULL, moment_id TEXT, offset_s REAL,
  ts REAL NOT NULL,         -- seconds since the epoch: ISO strings with offsets do not sort across DST
  text_hash BLOB NOT NULL,  -- blake2b-128 of the text: finds a vector to reuse
  UNIQUE (day, evidence_id)
);
-- Only the indexes a query uses: time and place filters apply inside FTS5 matches and the
-- vector index's per-day blocks, so neither needs a B-tree of its own.
CREATE INDEX units_day ON units(day, ts);
CREATE INDEX units_hash ON units(text_hash);
-- One row per embedded unit: int8 embedding for exact scoring, sign bits for the coarse pass.
CREATE TABLE vectors (id INTEGER PRIMARY KEY, bits BLOB NOT NULL, embedding BLOB NOT NULL);
-- External-content FTS5, kept in step by set statements per day in `_replace` and `_delete_day`
-- rather than row triggers: a trigger costs a statement per unit, ten times the whole write.
CREATE VIRTUAL TABLE units_fts USING fts5(text, content='units', content_rowid='id');
-- The knowledge graph (ingest/graph): entities the records name, which units mention them, and
-- how two entities met. Entities are shared by every day; mentions and links belong to the day
-- of the record that shows them and are replaced with it. An entity nothing points at any more
-- is dropped when a day is written.
CREATE TABLE entities (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, key TEXT NOT NULL, name TEXT NOT NULL,
  UNIQUE (kind, key)
);
CREATE TABLE mentions (
  unit_id INTEGER NOT NULL, entity_id INTEGER NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY (unit_id, entity_id, role)
) WITHOUT ROWID;
CREATE INDEX mentions_entity ON mentions(entity_id, unit_id);
CREATE TABLE links (
  day TEXT NOT NULL, src INTEGER NOT NULL, dst INTEGER NOT NULL, kind TEXT NOT NULL, evidence_id TEXT NOT NULL,
  PRIMARY KEY (day, src, dst, kind, evidence_id)
) WITHOUT ROWID;
CREATE INDEX links_src ON links(src);
CREATE INDEX links_dst ON links(dst);
CREATE TABLE speakers (moment_id TEXT, diar_label TEXT, person_id TEXT, confirmed INTEGER);
-- The notes folder (vault/notes) read back: see store/notes.py.
{NOTES}
CREATE TABLE deletions (t0 TEXT, t1 TEXT, reason TEXT, applied_at TEXT);
"""


DDL = DDL.replace("{NOTES}", note_index.DDL.strip())
# Tables and indexes added after schema 4: created in place, so no rebuild (and no re-embedding).
ADDITIVE = (
    note_index.LINKS_DDL
    + """
CREATE INDEX IF NOT EXISTS mentions_unit ON mentions(unit_id);
CREATE INDEX IF NOT EXISTS notes_identity ON notes(identity);
CREATE INDEX IF NOT EXISTS units_ts ON units(ts);
"""
)


def digest(text: str) -> bytes:
    return hashlib.blake2b(text.encode(), digest_size=16).digest()


@contextmanager
def _closing(db: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield db
    finally:
        db.close()


class LifeStore:
    """The vault's units across days. Writes hold ``vault.lock``; reads open their own connection
    (WAL lets them run beside a writer)."""

    def __init__(
        self, vault: Vault, embedder: Embedder | None = None, path: Path | None = None, zone: str = "America/Toronto"
    ) -> None:
        self.vault = vault
        self.zone = zone  # for days that have only computer capture (no day.json to say)
        self.path = path or vault.root / FILE_NAME
        self.embedder = embedder
        # Shared with the vault, so `Vault.invalidate_index` makes the next query re-check a day.
        self.fresh = vault.fresh_index
        self.index = VectorIndex()
        self._ready = False
        self.notes_root = vault.root / "notes"
        self._notes: dict[str, tuple[int, int]] | None = None  # the notes folder as last synced
        self._everything_at = -FULL_CHECK_S  # when every day's files were last checked (monotonic)

    @property
    def vectors(self) -> bool:
        return self.embedder is not None

    # -- connection ------------------------------------------------------------------------

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if not self.path.exists():
            self._forget()
        with _closing(sqlite3.connect(self.path, timeout=30, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous = NORMAL")  # WAL + NORMAL: safe for a rebuildable cache
            if not self._ready:
                self._migrate(db)
            yield db

    def _forget(self) -> None:
        """The file is gone: whatever was confirmed fresh or loaded lived in it."""
        self._ready = False
        self._notes = None
        self.fresh.clear()
        self.index.clear()

    def _migrate(self, db: sqlite3.Connection) -> None:
        db.execute("PRAGMA journal_mode = WAL")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            # Empty, or written by another version of this code: nothing in it is original, so
            # start over and let every day be indexed again.
            try:
                with self._write(db):
                    _drop_all(db)
                    _script(db, DDL)
                    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            except sqlite3.OperationalError as error:  # a table from an extension this Python cannot load
                raise RuntimeError(
                    f"{self.path} is from an incompatible build ({error}); delete it to rebuild"
                ) from error
            self.fresh.clear()
            self.index.clear()
            self._notes = None
        columns = {row[1] for row in db.execute("PRAGMA table_info(note_links)")}
        if columns and "name" not in columns:  # the first form of the table, from a development build
            db.execute("DROP TABLE note_links")
        _script(db, ADDITIVE)  # added in place to a database from before they existed
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("derived") != DERIVED_VERSION:
            with self._write(db):
                db.execute("DROP TABLE IF EXISTS note_links")
                _script(db, note_index.LINKS_DDL)
                db.execute("UPDATE days SET stamp = ''")  # every day is extracted again
                db.execute("INSERT INTO note_chunks_fts(note_chunks_fts) VALUES ('delete-all')")
                db.execute("DELETE FROM note_chunks")
                db.execute("DELETE FROM notes")
                db.execute("DELETE FROM links WHERE day = ''")
                db.execute("INSERT OR REPLACE INTO meta VALUES ('derived', ?)", (DERIVED_VERSION,))
            self.fresh.clear()
            self._notes = None
        if self.embedder is not None:
            wanted = {"model": self.embedder.name, "dim": str(self.embedder.dim)}
            if {key: meta.get(key) for key in wanted} != wanted:
                # Another model's vectors are not comparable with this one's: start over.
                with self._write(db):
                    db.execute("DELETE FROM vectors")
                    db.execute("UPDATE days SET model = NULL, unembedded = units, generation = ?", (_next(db),))
                    db.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", wanted.items())
        self._ready = True

    @staticmethod
    @contextmanager
    def _write(db: sqlite3.Connection) -> Iterator[None]:
        db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")

    # -- keeping days current --------------------------------------------------------------

    def has_day(self, day: str) -> bool:
        """A vault day folder (day.json) for the date; otherwise only its capture logs count."""
        return (self.vault.day_path(day, must_exist=False) / "day.json").is_file()

    def knows(self, day: str) -> bool:
        """A vault day or a day of computer capture, without listing every day."""
        if self.has_day(day):
            return True
        folder = self.vault.root / "capture" / day
        return folder.is_dir() and any(stored(folder / name).exists() for name in LOGS)

    def days(self) -> list[str]:
        """Every date with something to search: vault days and capture days, newest first."""
        return sorted({d["date"] for d in self.vault.list_days()} | set(capture_days(self.vault)), reverse=True)

    def _key(self, day: str) -> tuple:
        # The model is part of the key: a store with another (or no) embedder on the same vault
        # must look at the database again rather than trust this one's word.
        model = self.embedder.name if self.embedder else None
        snapshot = self.vault.snapshot_key(day) if self.has_day(day) else None
        capture = tuple(sorted((k, tuple(v)) for k, v in capture_stamp(self.vault, day).items()))
        return snapshot, self.vault.stamp(self.vault.root / "places.json"), model, capture

    def _stamp(self, day: str) -> str:
        """The source stamp of everything a day's units come from, as stored in ``days``."""
        stamp = source_stamp(self.vault.day_path(day)) if self.has_day(day) else {}
        return json.dumps({**stamp, **capture_stamp(self.vault, day)}, sort_keys=True)

    def sync_notes(self) -> int:
        """Read the notes folder into the database if any note changed; returns notes rewritten."""
        found = note_index.scan(self.notes_root)
        if not self.path.exists():
            self._forget()
        if found == self._notes and self._notes is not None:
            return 0
        with self.vault.lock, self.connect() as db, self._write(db):
            count = note_index.sync(db, self.notes_root, found, _entities(db, rename=False))
            _drop_orphans(db)
        self._notes = found
        return count

    def ensure(self, days: Iterable[str] | None = None, *, quick: bool = False) -> None:
        """Bring the given days (default: every day in the vault, and the notes) up to date. A
        repeat call with unchanged files costs a few stats per day. ``quick`` (search as you type,
        a request per pause in typing): across every day, older days and the notes are checked at
        most every ``FULL_CHECK_S``; today and yesterday, where capture is arriving, every time."""
        everything = days is None
        if not self.path.exists():
            self._forget()  # deleted underneath us: every day must be written again
            self._everything_at = -FULL_CHECK_S
        if everything and quick and monotonic() - self._everything_at < FULL_CHECK_S:
            today = datetime.now(ZoneInfo(self.zone)).date()
            recent = [(today - timedelta(days=n)).isoformat() for n in (1, 0)]
            days, everything = [d for d in recent if self.knows(d)], False
        elif everything:
            self._everything_at = monotonic()
            self.sync_notes()
        wanted = self.days() if everything else list(days)
        stale = [day for day in wanted if self.fresh.get(day) != self._key(day)]
        if not stale and not everything:
            return
        with self.connect() as db:
            known = {row["day"]: row for row in db.execute("SELECT day, stamp, model, unembedded FROM days")}
            if everything and (gone := set(known) - set(wanted)):
                with self.vault.lock, self._write(db):
                    for day in gone:
                        self._delete_day(db, day)
                    _drop_orphans(db)
        for day in stale:
            with self.vault.lock:
                key = self._key(day)
                stamp = self._stamp(day)
            row = known.get(day)
            changed = row is None or row["stamp"] != stamp
            # Vectors missing after an append, or after search was switched on: fill them now
            # if that is quick; a day with more waits for `daycube index`.
            fill = self.vectors and row is not None and 0 < row["unembedded"] <= INLINE_EMBED_LIMIT
            if changed or fill:
                self.update(day, embed_limit=INLINE_EMBED_LIMIT)
            else:
                self.fresh[day] = key

    def update(self, day: str, *, embed_limit: int | None = None) -> Record:
        """Replace the day's rows from its files. ``embed_limit`` caps how many new texts this
        call embeds (None: all); over the cap, the day is stored without the missing vectors."""
        for _ in range(3):
            with self.vault.lock:
                events: list[Record] = []
                places: dict[str, str] = {}
                if self.has_day(day):
                    self.vault.validate(day)
                    units = collect_units(self.vault, day)
                    snapshot = self.vault.snapshot(day)
                    zone = snapshot.day["tz"]
                    events = snapshot.events.records
                    renamed = self.vault.place_names()
                    places = {p["id"]: renamed.get(p["id"], p["name"]) for p in snapshot.day["places"]}
                elif day in capture_days(self.vault):
                    units, zone = [], self.zone
                else:
                    raise FileNotFoundError(f"No vault day or capture for {day}")
                capture = read_capture(self.vault, day)
                units += capture_units(self.vault, day, capture)
                graph = extract(day, units, capture, events, places)
                key = self._key(day)
                stamp = self._stamp(day)
            hashes = [digest(unit["text"]) for unit in units]
            vectors = self._vectors(day, units, hashes, embed_limit) if self.vectors else {}
            with self.vault.lock:
                if self._stamp(day) != stamp:
                    continue  # the day changed while its text was embedded: collect it again
                with self.connect() as db, self._write(db):
                    embedded = self._replace(db, day, zone, stamp, units, hashes, vectors)
                    self._graph(db, day, graph)
                self.fresh[day] = key
            engine = "sqlite-fts5+int8-vectors" if self.vectors else "sqlite-fts5"
            return {"date": day, "units": len(units), "engine": engine, "unembedded": len(units) - embedded}
        raise RuntimeError(f"{day} kept changing while it was being indexed")

    def _vectors(self, day: str, units: Sequence[Record], hashes: Sequence[bytes], limit: int | None) -> dict:
        """text hash -> (sign bits, int8 embedding), reused where the database already has one."""
        assert self.embedder is not None
        found: dict[bytes, tuple[bytes, bytes]] = {}
        reuse = "SELECT u.text_hash, v.bits, v.embedding FROM units u JOIN vectors v ON v.id = u.id WHERE "
        with self.connect() as db:
            found.update((row[0], (row[1], row[2])) for row in db.execute(reuse + "u.day = ?", (day,)))
            missing = list(dict.fromkeys(h for h in hashes if h not in found))
            for start in range(0, len(missing), CHUNK):
                chunk = missing[start : start + CHUNK]
                marks = ",".join("?" for _ in chunk)
                found.update(
                    (row[0], (row[1], row[2])) for row in db.execute(reuse + f"u.text_hash IN ({marks})", chunk)
                )
        texts = {h: unit["text"] for h, unit in zip(hashes, units, strict=True) if h not in found}
        if texts and (limit is None or len(texts) <= limit):
            for text_hash, vector in zip(texts, self.embedder.passages(list(texts.values())), strict=True):
                found[text_hash] = (sign_bits(vector), vector)
        return found

    def _replace(
        self,
        db: sqlite3.Connection,
        day: str,
        zone: str,
        stamp: str,
        units: Sequence[Record],
        hashes: Sequence[bytes],
        vectors: dict[bytes, tuple[bytes, bytes]],
    ) -> int:
        generation = _next(db)
        self._delete_day(db, day)
        # Inside BEGIN IMMEDIATE no other writer runs, so the new rows take the next ids in order.
        first = (db.execute("SELECT seq FROM sqlite_sequence WHERE name = 'units'").fetchone() or (0,))[0] + 1
        db.executemany(
            INSERT_UNIT,
            (
                (*(unit[c] for c in UNIT_COLUMNS), instant(unit["t"]).timestamp(), h)
                for unit, h in zip(units, hashes, strict=True)
            ),
        )
        db.execute("INSERT INTO units_fts(rowid, text) SELECT id, text FROM units WHERE day = ?", (day,))
        rows = [(first + i, *vectors[h]) for i, h in enumerate(hashes) if h in vectors]
        db.executemany("INSERT INTO vectors VALUES (?, ?, ?)", rows)
        embedded = len(rows)
        model = self.embedder.name if self.vectors else None
        db.execute(
            "INSERT INTO days VALUES (?, ?, ?, ?, ?, ?, ?)",
            (day, zone, stamp, len(units), model, len(units) - embedded, generation),
        )
        return embedded

    @staticmethod
    def _graph(db: sqlite3.Connection, day: str, graph: Graph) -> None:
        """The day's mentions and links, after its units (whose ids the mentions need)."""
        entity = _entities(db)

        units = dict(db.execute("SELECT evidence_id, id FROM units WHERE day = ?", (day,)).fetchall())
        db.executemany(
            "INSERT OR IGNORE INTO mentions VALUES (?, ?, ?)",
            [(units[e], entity(*target), role) for e, target, role in graph.mentions if e in units],
        )
        db.executemany(
            "INSERT OR IGNORE INTO links VALUES (?, ?, ?, ?, ?)",
            [(day, entity(*a), entity(*b), kind, evidence or "") for a, b, kind, evidence in graph.links],
        )
        _drop_orphans(db)

    @staticmethod
    def _delete_day(db: sqlite3.Connection, day: str) -> None:
        db.execute("DELETE FROM mentions WHERE unit_id IN (SELECT id FROM units WHERE day = ?)", (day,))
        db.execute("DELETE FROM links WHERE day = ?", (day,))
        db.execute(
            "INSERT INTO units_fts(units_fts, rowid, text) SELECT 'delete', id, text FROM units WHERE day = ?", (day,)
        )
        db.execute("DELETE FROM vectors WHERE id IN (SELECT id FROM units WHERE day = ?)", (day,))
        db.execute("DELETE FROM units WHERE day = ?", (day,))
        db.execute("DELETE FROM days WHERE day = ?", (day,))

    # -- queries -----------------------------------------------------------------------------

    @staticmethod
    def _filters(
        day: str | None, since: float | None, until: float | None, types: Sequence[str] | None, site: str | None
    ) -> tuple[str, list]:
        """SQL conditions on ``u`` (a units row) for a day, a time window, record kinds and a site."""
        clauses, values = [], []
        for clause, value in (("u.day = ?", day), ("u.ts >= ?", since), ("u.ts < ?", until)):
            if value is not None:
                clauses.append(f" AND {clause}")
                values.append(value)
        if types:
            clauses.append(f" AND u.type IN ({','.join('?' for _ in types)})")
            values += list(types)
        if site:
            clauses.append(
                " AND u.id IN (SELECT m.unit_id FROM mentions m JOIN entities e ON e.id = m.entity_id "
                "WHERE e.kind = 'site' AND (e.key = ? OR e.key LIKE ?))"
            )
            values += [site, f"%.{site}"]
        return "".join(clauses), values

    def keyword(
        self,
        expression: str,
        *,
        day: str | None = None,
        since: float | None = None,
        until: float | None = None,
        types: Sequence[str] | None = None,
        site: str | None = None,
        k: int,
    ) -> list[int]:
        """Unit ids matching an FTS5 expression, best BM25 first."""
        clauses, values = self._filters(day, since, until, types, site)
        sql = (
            "SELECT u.id FROM units_fts JOIN units u ON u.id = units_fts.rowid "
            f"WHERE units_fts MATCH ?{clauses} ORDER BY bm25(units_fts), u.ts LIMIT ?"
        )
        with self.connect() as db:
            return [row[0] for row in db.execute(sql, (expression, *values, k))]

    def listing(
        self,
        *,
        day: str | None = None,
        since: float | None = None,
        until: float | None = None,
        types: Sequence[str] | None = None,
        site: str | None = None,
        k: int,
    ) -> list[int]:
        """Unit ids within the filters, the latest ``k`` in time order (a question that is all filters)."""
        clauses, values = self._filters(day, since, until, types, site)
        sql = f"SELECT id FROM (SELECT u.id, u.ts FROM units u WHERE 1{clauses} ORDER BY u.ts DESC LIMIT ?) ORDER BY ts"
        with self.connect() as db:
            return [row[0] for row in db.execute(sql, (*values, k))]

    def allowed(self, ids: Sequence[int], *, types: Sequence[str] | None, site: str | None) -> set[int]:
        """Which of ``ids`` pass the kind and site filters (meaning search filters after the fact)."""
        if not ids or (not types and not site):
            return set(ids)
        clauses, values = self._filters(None, None, None, types, site)
        out: set[int] = set()
        with self.connect() as db:
            for start in range(0, len(ids), CHUNK):
                chunk = ids[start : start + CHUNK]
                marks = ",".join("?" for _ in chunk)
                out.update(
                    row[0]
                    for row in db.execute(
                        f"SELECT u.id FROM units u WHERE u.id IN ({marks}){clauses}", (*chunk, *values)
                    )
                )
        return out

    def nearest(
        self, question: str, *, day: str | None = None, since: float | None = None, until: float | None = None, k: int
    ) -> list[tuple[int, float]]:
        """(unit id, cosine similarity) of the ``k`` units closest in meaning, closest first."""
        if self.embedder is None:
            return []
        query = self.embedder.query(question)
        with self.connect() as db:
            return self.index.search(db, query, k, days=None if day is None else [day], since=since, until=until)

    def note_keyword(self, expression: str, *, k: int) -> list[int]:
        """Note chunk ids matching an FTS5 expression, best BM25 first."""
        sql = (
            "SELECT c.id FROM note_chunks_fts JOIN note_chunks c ON c.id = note_chunks_fts.rowid "
            "WHERE note_chunks_fts MATCH ? ORDER BY bm25(note_chunks_fts) LIMIT ?"
        )
        with self.connect() as db:
            return [row[0] for row in db.execute(sql, (expression, k))]

    def note_rows(self, ids: Sequence[int]) -> dict[int, Record]:
        found: dict[int, Record] = {}
        with self.connect() as db:
            for start in range(0, len(ids), CHUNK):
                chunk = ids[start : start + CHUNK]
                marks = ",".join("?" for _ in chunk)
                for row in db.execute(
                    f"SELECT c.*, n.title, n.identity, n.mtime_ns FROM note_chunks c JOIN notes n ON n.path = c.path "
                    f"WHERE c.id IN ({marks})",
                    chunk,
                ):
                    found[row["id"]] = dict(row)
        # Searches across days re-read the notes folder at most every FULL_CHECK_S: never return a
        # note deleted since, and bring the folder in now if a hit's file changed.
        stale = False
        for path in {row["path"] for row in found.values()}:
            try:
                current = os.stat(self.notes_root / path).st_mtime_ns
            except OSError:
                current = None
            if current is None:
                found = {k: v for k, v in found.items() if v["path"] != path}
            stale |= current != next((r["mtime_ns"] for r in found.values() if r["path"] == path), current)
            stale |= current is None
        if stale:
            self.sync_notes()
        return found

    def rows(self, ids: Sequence[int]) -> dict[int, Record]:
        found: dict[int, Record] = {}
        with self.connect() as db:
            for start in range(0, len(ids), CHUNK):
                chunk = ids[start : start + CHUNK]
                marks = ",".join("?" for _ in chunk)
                for row in db.execute(f"SELECT * FROM units WHERE id IN ({marks})", chunk):
                    found[row["id"]] = dict(row)
        return found

    def graph(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        kinds: Sequence[str] | None = None,
        limit: int = 200,
    ) -> Record:
        """The most mentioned entities in a time window, and the links among them."""
        self.ensure(quick=True)
        with self.connect() as db:
            view = graph_view(db, since=since, until=until, kinds=kinds, limit=limit)
        names = self.vault.place_names()  # a rename shows at once, before the day is indexed again
        for node in view["nodes"]:
            if node["kind"] == "place" and node["key"] in names:
                node["name"] = names[node["key"]]
        return view

    def entity(self, entity_id: int, *, limit: int = 50) -> Record | None:
        """One entity: what it is, the records that mention it (newest first), its neighbours."""
        self.ensure()
        with self.connect() as db:
            found = entity_view(db, entity_id, limit=limit)
        if found is not None and found["kind"] == "place":
            found["name"] = self.vault.place_names().get(found["key"], found["name"])
        return found

    def tables(self) -> Record:
        """What the database holds: rows per table, units per kind, entities per kind, links per kind."""
        with self.connect() as db:
            count = {
                name: db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]  # noqa: S608 - fixed names
                for name in ("days", "units", "vectors", "entities", "mentions", "links", "notes", "note_chunks")
            }
            return {
                "rows": count,
                "units_by_type": dict(
                    db.execute("SELECT type, count(*) FROM units GROUP BY type ORDER BY 2 DESC").fetchall()
                ),
                "entities_by_kind": dict(
                    db.execute("SELECT kind, count(*) FROM entities GROUP BY kind ORDER BY 2 DESC").fetchall()
                ),
                "links_by_kind": dict(
                    db.execute("SELECT kind, count(*) FROM links GROUP BY kind ORDER BY 2 DESC").fetchall()
                ),
                "unembedded": db.execute("SELECT coalesce(sum(unembedded), 0) FROM days").fetchone()[0],
                "schema": db.execute("PRAGMA user_version").fetchone()[0],
                "days": [
                    dict(r)
                    for r in db.execute("SELECT day, units, unembedded, model FROM days ORDER BY day DESC LIMIT 60")
                ],
            }

    def stats(self) -> Record:
        with self.connect() as db:
            days, units, unembedded = db.execute(
                "SELECT count(*), coalesce(sum(units), 0), coalesce(sum(unembedded), 0) FROM days"
            ).fetchone()
        size = 0
        for part in self.path.parent.glob(self.path.name + "*"):  # the database and its WAL
            with suppress(FileNotFoundError):
                size += part.stat().st_size
        return {
            "days": days,
            "units": units,
            "unembedded": unembedded,
            "model": self.embedder.name if self.embedder else None,
            "bytes": size,
        }


def _entities(db: sqlite3.Connection, rename: bool = True):
    """``entity(kind, key, name) -> id``: find or create an entity in the open transaction (the
    latest name wins unless ``rename`` is false)."""
    ids: dict[tuple[str, str], int] = {}
    on_conflict = "DO UPDATE SET name = excluded.name" if rename else "DO UPDATE SET name = name"

    def entity(kind: str, key: str, name: str) -> int:
        found = ids.get((kind, key))
        if found is None:
            found = db.execute(
                f"INSERT INTO entities(kind, key, name) VALUES (?, ?, ?) ON CONFLICT(kind, key) {on_conflict} RETURNING id",
                (kind, key, name),
            ).fetchone()[0]
            ids[(kind, key)] = found
        return found

    return entity


def _drop_orphans(db: sqlite3.Connection) -> None:
    db.execute(
        "DELETE FROM entities WHERE NOT EXISTS (SELECT 1 FROM mentions WHERE entity_id = entities.id) "
        "AND NOT EXISTS (SELECT 1 FROM links WHERE src = entities.id OR dst = entities.id)"
    )


def _drop_all(db: sqlite3.Connection) -> None:
    """Every trigger, then every table, virtual tables first (they drop their own shadow tables).
    Dropping an AUTOINCREMENT table also drops its ``sqlite_sequence`` row."""
    for kind in ("trigger", "table"):
        names = db.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%' "
            "ORDER BY sql LIKE 'CREATE VIRTUAL%' DESC",
            (kind,),
        ).fetchall()
        for (name,) in names:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone():
                db.execute(f'DROP {kind} "{name}"')


def _next(db: sqlite3.Connection) -> int:
    """The next value of the vault-wide write counter, inside the caller's transaction."""
    row = db.execute(
        "INSERT INTO meta VALUES ('generation', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1 RETURNING value"
    ).fetchone()
    return int(row[0])


def _script(db: sqlite3.Connection, script: str) -> None:
    """``executescript`` commits first, which would end the open transaction; run statements one
    by one instead, each complete at a line ending in a semicolon."""
    statement: list[str] = []
    for line in script.splitlines():
        if line.lstrip().startswith("--"):
            continue
        statement.append(line)
        if line.rstrip().endswith(";") and sqlite3.complete_statement("\n".join(statement)):
            db.execute("\n".join(statement))
            statement = []

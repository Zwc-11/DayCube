"""The plain-file vault: atomic writes, stamp-keyed snapshots and memoized validation.

Every read path goes through :class:`DaySnapshot`, a parse of one day's files that is reused until
any of those files changes. Each section is parsed and schema-checked once, on first use, so a
request touches only the files it needs and repeated requests cost a few ``stat`` calls.
Snapshots and serialized days live in size-bounded segmented LRU caches, so memory stays flat
however many days a session opens. Event logs grow by appending, and the vault folds its own
appends into the cached snapshot instead of parsing the day again.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import heapq
import json
import os
import re
import secrets
import shutil
import tempfile
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Container
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

import zstandard

from . import contracts
from .cache import SegmentedLRU
from .config import PROJECT_ROOT
from .time import instant, tod
from .trajectory import simplify

Record = dict[str, Any]
# (mtime_ns, size, inode); None when the file is missing. Atomic replacement always yields a new
# inode, and an in-process write counter (see Vault._writes) covers coarse filesystem clocks.
FileStamp = tuple[int, int, int] | None
SNAPSHOT_FILES = ("day.json", "events.jsonl", "skeleton.geojson", "screens/ocr.jsonl", "graph.json")
MOMENT_ID = re.compile(r"[A-Za-z0-9_-]+")
# Cache budgets. Snapshots are measured in bytes of the files they parse (parsed objects take a
# few times more); payloads in serialized bytes.
SNAPSHOT_CACHE_BYTES = 64 << 20
PAYLOAD_CACHE_BYTES = 32 << 20
# Finished days can be compacted: large logs become zstd frames beside their name (events.jsonl
# -> events.jsonl.zst) and every reader decompresses transparently. Level 10 gets within a few
# percent of the maximum ratio at a fraction of its time.
COMPACTABLE = ("events.jsonl", "skeleton.geojson", "screens/ocr.jsonl")
COMPACT_MIN_BYTES = 4096
ZSTD_LEVEL = 10
# The viewer path: 10 cm, well under one voxel, and the fields the viewer's contract reads.
PATH_TOLERANCE_M = 0.1
PATH_FIELDS = ("t", "x", "y", "floor", "pos_conf", "fidelity")


def atomic_bytes(path: Path, data: bytes) -> FileStamp:
    """Write to a temporary file, fsync, then rename over ``path``: readers see old or new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".daycube-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            stat = os.fstat(stream.fileno())
        os.replace(temporary, path)
        return stat.st_mtime_ns, stat.st_size, stat.st_ino
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_text(path: Path, content: str) -> FileStamp:
    return atomic_bytes(path, content.encode("utf-8"))


def packed(path: Path) -> Path:
    return path.with_name(path.name + ".zst")


def stored(path: Path) -> Path:
    """The file holding a logical vault file: itself, else its compacted copy."""
    if path.exists():
        return path
    compacted = packed(path)
    return compacted if compacted.exists() else path


def logical_size(path: Path) -> int:
    """Bytes of the file's content, also when it is stored compacted (from the frame header)."""
    source = stored(path)
    if source is path:
        return file_stamp(path)[1] if path.exists() else 0
    with source.open("rb") as stream:
        size = zstandard.frame_content_size(stream.read(18))
    return max(size, 0)


def read_text(path: Path) -> str:
    source = stored(path)
    if source is path:
        return path.read_text(encoding="utf-8")
    return zstandard.ZstdDecompressor().decompress(source.read_bytes()).decode("utf-8")


def append_text(path: Path, content: str) -> FileStamp:
    """Append complete lines in one write and fsync: O(appended bytes), not O(file).

    The vault acknowledges an append only after fsync, so a crash can leave at most one
    unacknowledged partial line at the end. It is cut before the next append. A final record that
    is merely missing its newline, as an editor may leave it, is kept.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab+") as stream:
        size = stream.seek(0, os.SEEK_END)
        if size:
            start = _last_line_start(stream, size)
            stream.seek(start)
            tail = stream.read()
            if tail and not tail.endswith(b"\n"):
                try:
                    json.loads(tail)
                    stream.write(b"\n")
                except ValueError:
                    stream.truncate(start)
        stream.write(content.encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
        stat = os.fstat(stream.fileno())
    return stat.st_mtime_ns, stat.st_size, stat.st_ino


def _last_line_start(stream: Any, size: int, chunk: int = 1 << 16) -> int:
    """Offset just past the last newline before the final byte, scanning backwards."""
    end = size - 1
    while end > 0:
        begin = max(0, end - chunk)
        stream.seek(begin)
        found = stream.read(end - begin).rfind(b"\n")
        if found >= 0:
            return begin + found + 1
        end = begin
    return 0


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def read_lines(path: Path) -> list[Record]:
    """JSON lines. A final fragment that does not parse is an append in flight or torn by a
    crash, never acknowledged, so it is skipped rather than failing the whole log."""
    if not stored(path).exists():
        return []
    lines = read_text(path).split("\n")
    tail = lines.pop()
    records = [json.loads(line) for line in lines if line.strip()]
    if tail.strip():
        with contextlib.suppress(ValueError):
            records.append(json.loads(tail))
    return records


def file_stamp(path: Path) -> FileStamp:
    try:
        stat = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return stat.st_mtime_ns, stat.st_size, stat.st_ino


@dataclass(frozen=True, slots=True)
class EventSeries:
    """Events sorted by start, with parsed instants in parallel arrays for O(log n) windows."""

    records: tuple[Record, ...]
    starts: tuple[datetime, ...]
    ends: tuple[datetime, ...]
    # Longest event; any event overlapping [t0, t1) starts at or after t0 - max_span.
    max_span: timedelta

    @classmethod
    def build(cls, records: list[Record]) -> EventSeries:
        # Decorate once: each timestamp is parsed a single time. The unique index breaks ties, so
        # the sort is stable and never compares the records themselves.
        keyed = sorted((instant(r["t"]), i, r) for i, r in enumerate(records))
        starts = tuple(start for start, _, _ in keyed)
        ends = tuple(instant(r["t_end"]) if r.get("t_end") else start for start, _, r in keyed)
        spans = (end - start for start, end in zip(starts, ends, strict=True))
        return cls(
            records=tuple(r for _, _, r in keyed),
            starts=starts,
            ends=ends,
            max_span=max(timedelta(0), max(spans, default=timedelta(0))),
        )

    def extend(self, records: list[Record]) -> EventSeries:
        """The series after appending ``records`` to its log, without re-parsing the log.

        Equal to ``build`` over the whole log: a stable merge keeps existing records ahead of new
        ones that start at the same instant, as the log order does. O(n + k log k).
        """
        added = EventSeries.build(records)
        merged = list(
            heapq.merge(
                zip(self.starts, self.ends, self.records, strict=True),
                zip(added.starts, added.ends, added.records, strict=True),
                key=lambda item: item[0],
            )
        )
        return EventSeries(
            records=tuple(r for _, _, r in merged),
            starts=tuple(s for s, _, _ in merged),
            ends=tuple(e for _, e, _ in merged),
            max_span=max(self.max_span, added.max_span),
        )

    def window(self, t0: datetime, t1: datetime) -> list[Record]:
        """Events overlapping [t0, t1); zero-length events count when they start inside it."""
        lo = bisect_left(self.starts, t0 - self.max_span)
        hi = bisect_left(self.starts, t1)
        return [
            self.records[i]
            for i in range(lo, hi)
            if (self.ends[i] > t0 if self.ends[i] > self.starts[i] else self.starts[i] >= t0)
        ]


@dataclass(frozen=True, slots=True)
class Moment:
    meta: Record
    transcript: Record


class DaySnapshot:
    """One parse of a day folder, keyed by the stamps of every file it may read.

    Sections load lazily and are schema-checked as they load, so every consumer sees records that
    passed their contract. Snapshots are shared between requests: treat their records as
    read-only and copy before changing anything.
    """

    def __init__(self, path: Path, key: tuple) -> None:
        self.path = path
        self.key = key

    @property
    def source_bytes(self) -> int:
        """Content size of the files behind the key: the cache charges a snapshot this much."""
        return sum(logical_size(self.path / name) for name, stamp in self.key if stamp[0] is not None)

    def successor(self, key: tuple, **sections: Any) -> DaySnapshot:
        """A snapshot for ``key`` that reuses every section already parsed here.

        Only for keys that differ from this one in files whose new content is given in
        ``sections``; the caller has checked that no other file changed.
        """
        nxt = DaySnapshot(self.path, key)
        nxt.__dict__.update({k: v for k, v in self.__dict__.items() if k not in ("path", "key")})
        nxt.__dict__.update(sections)
        return nxt

    @cached_property
    def day(self) -> Record:
        data = read_json(self.path / "day.json")
        contracts.validate("day", data)
        return data

    @cached_property
    def places(self) -> dict[str, Record]:
        return {place["id"]: place for place in self.day["places"]}

    @cached_property
    def events(self) -> EventSeries:
        records = read_lines(self.path / "events.jsonl")
        contracts.validate_all("event", records)
        return EventSeries.build(records)

    @cached_property
    def skeleton(self) -> Record:
        data = read_json(self.path / "skeleton.geojson")
        contracts.validate("skeleton", data)
        return data

    @cached_property
    def ocr(self) -> tuple[Record, ...]:
        records = read_lines(self.path / "screens/ocr.jsonl")
        contracts.validate_all("ocr", records)
        return tuple(records)

    @cached_property
    def moments(self) -> dict[str, Moment]:
        moments = {}
        for meta_path in sorted((self.path / "moments").glob("*/meta.json")):
            meta = read_json(meta_path)
            contracts.validate("moment", meta)
            transcript = read_json(meta_path.parent / "transcript.json")
            contracts.validate("transcript", transcript)
            moments[meta_path.parent.name] = Moment(meta, transcript)
        return moments

    @cached_property
    def graph(self) -> Record:
        data = read_json(self.path / "graph.json")
        contracts.validate("graph", data)
        return data


class Vault:
    """One writer process; atomic replacements prevent partially read JSON files."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.lock = RLock()
        # Writes made by this process, per file. Part of every cache key, so a change is visible
        # immediately even where the filesystem clock is coarser than two consecutive writes.
        self._writes: Counter[Path] = Counter()
        self._snapshots: SegmentedLRU[str, DaySnapshot] = SegmentedLRU(SNAPSHOT_CACHE_BYTES)
        self._validated: dict[str, tuple] = {}
        self._payloads: SegmentedLRU[str, tuple[tuple, bytes, str]] = SegmentedLRU(PAYLOAD_CACHE_BYTES)
        self._venues: dict[Path, tuple[tuple, Record]] = {}
        self._names: tuple[tuple, dict[str, str]] | None = None
        self._summaries: dict[Path, tuple[tuple, Record]] = {}
        # day -> key of the inputs the vault database was last confirmed fresh for.
        self.fresh_index: dict[str, tuple] = {}

    # -- files -------------------------------------------------------------------------------

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        token_path = self.root / ".token"
        try:
            with token_path.open("x", encoding="utf-8") as stream:
                stream.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
        if not (self.root / "places.json").exists():
            self.write_json(self.root / "places.json", {})

    @property
    def token(self) -> str:
        return (self.root / ".token").read_text(encoding="utf-8").strip()

    def day_path(self, value: str, *, must_exist: bool = True) -> Path:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Date must be YYYY-MM-DD")
        Date.fromisoformat(value)
        path = self.safe_path(value)
        if must_exist and not (path / "day.json").is_file():
            raise FileNotFoundError(f"No vault day: {value}")
        return path

    def safe_path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("Path escapes the vault")
        return candidate

    def stamp(self, path: Path) -> tuple[FileStamp, int]:
        # Vault paths are built from the resolved root, so they compare equal without resolve().
        return file_stamp(path), self._writes[path]

    def write_json(self, path: Path, value: Any, *, compact: bool = False) -> tuple[FileStamp, int]:
        """Indented for files people read and edit; ``compact`` for machine-written streams,
        where indentation doubles the size of a 1 Hz skeleton."""
        text = (
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if compact
            else json.dumps(value, ensure_ascii=False, indent=2)
        )
        return self._replace(path, text + "\n")

    def write_lines(self, path: Path, values: list[Record]) -> tuple[FileStamp, int]:
        return self._replace(path, "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values))

    def append_lines(self, path: Path, values: list[Record]) -> tuple[FileStamp, int]:
        with self.lock:
            self._expand(path)
            stamp = append_text(path, "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values))
            self._writes[path] += 1
            return stamp, self._writes[path]

    def _replace(self, path: Path, text: str) -> tuple[FileStamp, int]:
        with self.lock:
            stamp = atomic_text(path, text)
            self._writes[path] += 1
            self._drop_packed(path)
            return stamp, self._writes[path]

    def _expand(self, path: Path) -> None:
        """Before appending to a compacted log, turn it back into plain text."""
        if not path.exists() and packed(path).exists():
            atomic_text(path, read_text(path))
            self._writes[path] += 1
            self._drop_packed(path)

    def _drop_packed(self, path: Path) -> None:
        if packed(path).exists():
            packed(path).unlink()
            self._writes[packed(path)] += 1

    def compact_day(self, day: str) -> dict[str, list[int]]:
        """Compress a finished day's large logs. Returns ``{file: [bytes before, bytes after]}``.

        Each copy is verified by decompressing it before the plain file is removed. Snapshots,
        validation and the index see a new stamp and reload once; their content is identical.
        """
        report = {}
        with self.lock:
            path = self.day_path(day)
            targets = [path / name for name in COMPACTABLE] + sorted((self.root / "capture" / day).glob("*.jsonl"))
            for plain in targets:
                if not plain.is_file() or plain.stat().st_size < COMPACT_MIN_BYTES:
                    continue
                data = plain.read_bytes()
                frame = zstandard.ZstdCompressor(level=ZSTD_LEVEL).compress(data)
                if zstandard.ZstdDecompressor().decompress(frame) != data:
                    raise RuntimeError(f"Compressed copy of {plain.name} does not round-trip")
                atomic_bytes(packed(plain), frame)
                self._writes[packed(plain)] += 1
                plain.unlink()
                self._writes[plain] += 1
                report[plain.relative_to(self.root).as_posix()] = [len(data), len(frame)]
        return report

    # -- snapshots ---------------------------------------------------------------------------

    def snapshot_key(self, day: str) -> tuple:
        path = self.day_path(day)
        files = [path / name for name in SNAPSHOT_FILES]
        moments = path / "moments"
        if moments.is_dir():
            for entry in sorted(moments.iterdir()):
                files += [entry / "meta.json", entry / "transcript.json"]
        return tuple((f.relative_to(path).as_posix(), self.stamp(stored(f))) for f in files)

    def snapshot(self, day: str) -> DaySnapshot:
        """The current snapshot, rebuilt only when one of its files changed."""
        key = self.snapshot_key(day)
        with self.lock:
            current = self._snapshots.get(day)
            if current is None or current.key != key:
                current = DaySnapshot(self.day_path(day), key)
                self._snapshots.put(day, current, current.source_bytes)
            return current

    def place_names(self) -> dict[str, str]:
        """User-chosen place names. Shared between callers: read-only."""
        path = self.root / "places.json"
        key = self.stamp(path)
        cached = self._names
        if cached is None or cached[0] != key:
            cached = (key, read_json(path))
            self._names = cached
        return cached[1]

    def venue(self, relative: str) -> Record:
        path = (PROJECT_ROOT / relative).resolve()
        if not path.is_relative_to(PROJECT_ROOT / "venues"):
            raise ValueError("Venue path must stay in venues/")
        key = self.stamp(path)
        cached = self._venues.get(path)
        if cached is None or cached[0] != key:
            data = read_json(path)
            contracts.validate("venue", data)
            cached = (key, data)
            self._venues[path] = cached
        return cached[1]

    def venue_stamps(self, snapshot: DaySnapshot) -> tuple:
        return tuple(
            self.stamp((PROJECT_ROOT / p["venue"]).resolve()) for p in snapshot.day["places"] if p.get("venue")
        )

    def validation_key(self, snapshot: DaySnapshot) -> tuple:
        return snapshot.key, self.venue_stamps(snapshot)

    # -- reads -------------------------------------------------------------------------------

    def list_days(self) -> list[Record]:
        """Newest first. Each day.json is read once per change; the listing never fills the
        snapshot cache, so browsing the calendar cannot evict the day being worked on."""
        result = []
        for path in sorted(self.root.glob("????-??-??/day.json"), reverse=True):
            key = self.stamp(path)
            cached = self._summaries.get(path)
            if cached is None or cached[0] != key:
                day = read_json(path)
                places = sorted(day["places"], key=lambda p: p["layer_index"])
                cached = (key, {"date": day["date"], "stats": day["stats"], "kinds": [p["kind"] for p in places]})
                self._summaries[path] = cached
            result.append(cached[1])
        return result

    def events(self, day: str) -> list[Record]:
        """Events in start order, as deep copies: callers may change them freely."""
        return copy.deepcopy(list(self.snapshot(day).events.records))

    def events_in(self, day: str, t0: str | None, t1: str | None, kind: str | None) -> list[Record]:
        if bool(t0) != bool(t1):
            raise ValueError("Provide both t0 and t1, or neither")
        series = self.snapshot(day).events
        if t0 and t1:
            lower, upper = instant(t0), instant(t1)
            if lower >= upper:
                raise ValueError("t1 must be after t0")
            records = series.window(lower, upper)
        else:
            records = list(series.records)
        return records if kind is None else [r for r in records if r["type"] == kind]

    def has_place(self, place_id: str) -> bool:
        return any(
            place_id in {p["id"] for p in read_json(self.day_path(d["date"]) / "day.json")["places"]}
            for d in self.list_days()
        )

    # -- writes ------------------------------------------------------------------------------

    def seed(self) -> str:
        fixture = PROJECT_ROOT / "fixtures/sample-day"
        day = read_json(fixture / "day.json")["date"]
        destination = self.day_path(day, must_exist=False)
        with self.lock:
            if destination.exists():
                raise FileExistsError(f"Day {day} already exists; seed never overwrites a day")
            shutil.copytree(fixture, destination)
        self.validate(day)
        return day

    def append_events(self, day: str, events: list[Record]) -> int:
        with self.lock:
            path = self.day_path(day)
            snapshot = self.snapshot(day)
            before = self.validation_key(snapshot)
            was_valid = self._validated.get(day) == before
            data = snapshot.day
            for event in events:
                self.validate_event(event, day, data["tz"], snapshot.places.keys())
                moment_id = event.get("moment_id")
                if moment_id and (
                    not MOMENT_ID.fullmatch(moment_id) or not (path / "moments" / moment_id / "meta.json").is_file()
                ):
                    raise ValueError("Event references a missing moment")
            current = snapshot.events.records
            known_ids = {e["id"] for e in current}
            incoming = [e["id"] for e in events]
            if len(set(incoming)) != len(incoming) or known_ids.intersection(incoming):
                raise ValueError("Event ids must be unique within a day")
            # Sections load lazily: only trust them if the files still match the key afterwards.
            unchanged = self.snapshot_key(day) == snapshot.key
            series = snapshot.events.extend(events)
            written = {"events.jsonl": self.append_lines(path / "events.jsonl", events)}
            stats = self._stats(data, series.records, sum(1 for _ in (path / "moments").glob("*/meta.json")))
            day_data = dict(data, stats=stats)
            written["day.json"] = self.write_json(path / "day.json", day_data)
            self.invalidate_index(day)
            # Write through: if only our two files changed, the next reader gets the merged series
            # instead of parsing and schema-checking the whole log again.
            expected = tuple((name, written.get(name, stamp)) for name, stamp in snapshot.key)
            if unchanged and self.snapshot_key(day) == expected:
                successor = snapshot.successor(expected, day=day_data, events=series)
                self._snapshots.put(day, successor, successor.source_bytes)
                # A valid day plus events that passed the same per-event and uniqueness checks,
                # with recomputed stats, is still valid.
                if was_valid:
                    self._carry_validation(day, before, written)
        return len(events)

    @staticmethod
    def validate_event(event: Record, day: str, timezone: str, places: Container[str]) -> None:
        contracts.validate("event", event)
        start = instant(event["t"])
        end = instant(event["t_end"]) if event.get("t_end") else start
        check_event(event, start, end, start.astimezone(ZoneInfo(timezone)).date().isoformat(), day, places)

    def invalidate_index(self, day: str) -> None:
        """Make the next search re-check this day's files against the vault database."""
        self.fresh_index.pop(day, None)

    def refresh_stats(self, day: str) -> None:
        snapshot = self.snapshot(day)
        self._write_stats(day, snapshot.day, list(snapshot.events.records))

    def _write_stats(self, day: str, data: Record, events: list[Record]) -> tuple[FileStamp, int]:
        path = self.day_path(day)
        moments = sum(1 for _ in (path / "moments").glob("*/meta.json"))
        return self.write_json(path / "day.json", dict(data, stats=self._stats(data, events, moments)))

    @staticmethod
    def _stats(data: Record, events: list[Record] | tuple[Record, ...], moments: int) -> Record:
        return {
            "events": len(events),
            "moments": moments,
            "places": len(data["places"]),
            "floor_changes": sum(e["type"] == "place.floor" for e in events),
        }

    # -- validation --------------------------------------------------------------------------

    def _carry_validation(self, day: str, before: tuple, written: dict[str, tuple]) -> None:
        files, venues = before
        expected = tuple((name, written.get(name, stamp)) for name, stamp in files)
        after = self.validation_key(self.snapshot(day))
        if after == (expected, venues):
            self._validated[day] = after

    def validate(self, day: str) -> Record:
        """Check every file and cross-reference of a day. Memoized until any of its files changes."""
        snapshot = self.snapshot(day)
        events = snapshot.events.records
        summary = {"date": day, "valid": True, "events": len(events), "moments": len(snapshot.moments)}
        key = self.validation_key(snapshot)
        if self._validated.get(day) == key:
            return summary
        data = snapshot.day
        if data["date"] != day:
            raise ValueError("Folder date differs from day.json")
        ZoneInfo(data["tz"])
        places = snapshot.places
        if len(places) != len(data["places"]):
            raise ValueError("Duplicate place visit ids")
        layer_ids = [p["layer_index"] for p in data["places"]]
        if len(set(layer_ids)) != len(layer_ids):
            raise ValueError("Duplicate layer indices")
        for place in data["places"]:
            if instant(place["t1"]) <= instant(place["t0"]):
                raise ValueError("Visit must end after it starts")
            if place.get("venue"):
                self.venue(place["venue"])
        for feature in snapshot.skeleton["features"]:
            properties = feature["properties"]
            instant(properties["t"])
            if properties["place"] is not None and properties["place"] not in places:
                raise ValueError("Skeleton references an unknown place")
        ids = {e["id"] for e in events}
        if len(ids) != len(events):
            raise ValueError("Duplicate event ids")
        moments = snapshot.moments
        zone = ZoneInfo(data["tz"])
        series = snapshot.events
        # Schema checks ran when the snapshot loaded; these are the cross-record rules, reusing
        # the instants parsed for the series instead of parsing every timestamp again.
        for event, start, end in zip(events, series.starts, series.ends, strict=True):
            check_event(event, start, end, start.astimezone(zone).date().isoformat(), day, places)
            if event.get("moment_id") and event["moment_id"] not in moments:
                raise ValueError("Event references a missing moment")
        if not set(data["cube_faces"]) <= ids | moments.keys():
            raise ValueError("Cube faces reference missing records")
        snapshot.ocr  # noqa: B018 - loading the section schema-checks every OCR record
        for moment_id, moment in moments.items():
            meta = moment.meta
            if meta["id"] != moment_id or (meta["place"] is not None and meta["place"] not in places):
                raise ValueError("Moment id/place does not match its vault location")
            for segment in moment.transcript["segments"]:
                if not 0 <= segment["start"] < segment["end"] <= meta["duration_s"]:
                    raise ValueError("Transcript segment lies outside its moment")
        graph = snapshot.graph
        nodes = {node["id"] for node in graph["nodes"]}
        if len(nodes) != len(graph["nodes"]) or any(
            edge["from"] not in nodes or edge["to"] not in nodes for edge in graph["edges"]
        ):
            raise ValueError("Graph contains duplicate nodes or dangling edges")
        if data["stats"] != self._stats(data, events, len(moments)):
            raise ValueError("Day stats differ from the vault records")
        self._validated[day] = key
        return summary

    # -- viewer day --------------------------------------------------------------------------

    def load_day(self, day: str) -> Record:
        """The viewer's day: one layer per visit, in layer order. Returns a new structure."""
        return json.loads(self.day_payload(day))

    def _build_day(self, snapshot: DaySnapshot) -> Record:
        """Shares nested objects with the snapshot: serialize, never hand out."""
        data = snapshot.day
        zone = data["tz"]
        names = self.place_names()
        events_by_place: dict[str | None, list[Record]] = defaultdict(list)
        fixes_by_place: dict[str | None, list[Record]] = defaultdict(list)
        for event in snapshot.events.records:
            events_by_place[event.get("place")].append(event)
        for fix in snapshot.skeleton["features"]:
            fixes_by_place[fix["properties"]["place"]].append(fix["properties"])
        layers = []
        for place in sorted(data["places"], key=lambda p: p["layer_index"]):
            venue = self.venue(place["venue"]) if place.get("venue") else None
            pins = [dict(e, tod=tod(e["t"], zone)) for e in events_by_place[place["id"]]]
            layers.append(
                {
                    "index": place["layer_index"],
                    "placeId": place["id"],
                    "name": names.get(place["id"], place["name"]),
                    "kind": place["kind"],
                    "t0": tod(place["t0"], zone),
                    "t1": tod(place["t1"], zone),
                    "floors": place["floors_visited"],
                    "floorHeight": venue["floor_height_m"] if venue else 3.3,
                    "frame": {
                        "lat": place["lat"],
                        "lon": place["lon"],
                        "axisDeg": venue["axis_deg"] if venue else 0,
                        "size": [20, 14],
                    },
                    "geometry": {
                        "fidelity": "illustrative",
                        "footprint": [[-10, -7], [10, -7], [10, 7], [-10, 7]],
                        "levels": venue["levels"] if venue else [],
                    },
                    "path": viewer_path(fixes_by_place[place["id"]], zone),
                    "events": pins,
                    "quotes": [e for e in pins if e["type"] == "said.quote"],
                    "moments": sorted({e["moment_id"] for e in pins if e.get("moment_id")}),
                }
            )
        return {**data, "layers": layers}

    def day_payload(self, day: str) -> bytes:
        """``load_day`` serialized once and replayed until an input file changes."""
        return self.tagged_day(day)[0]

    def tagged_day(self, day: str) -> tuple[bytes, str]:
        """The serialized day and its entity tag, a hash of the bytes. Clients that send the tag
        back get a 304 and keep their parsed copy."""
        snapshot = self.snapshot(day)
        key = (snapshot.key, self.stamp(self.root / "places.json"), self.venue_stamps(snapshot))
        cached = self._payloads.get(day)
        if cached is None or cached[0] != key:
            body = json.dumps(self._build_day(snapshot), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            cached = (key, body, f'"{hashlib.blake2b(body, digest_size=12).hexdigest()}"')
            self._payloads.put(day, cached, len(body))
        return cached[1], cached[2]


def viewer_path(fixes: list[Record], zone: str) -> list[Record]:
    """A visit's fixes as the viewer draws them: its fields only, in time order, simplified so
    the interpolated position at every fix time stays within PATH_TOLERANCE_M. Stationary
    stretches collapse to their two ends. The full fixes stay available as the skeleton."""
    points = sorted(
        ({**{k: f.get(k) for k in PATH_FIELDS}, "t": tod(f["t"], zone)} for f in fixes), key=lambda p: p["t"]
    )
    return simplify(points, PATH_TOLERANCE_M)


def check_event(
    event: Record, start: datetime, end: datetime, local_date: str, day: str, places: Container[str]
) -> None:
    """Rules a single event must satisfy beyond its schema."""
    if local_date != day:
        raise ValueError("Event timestamp is outside the selected local day")
    if end < start:
        raise ValueError("Event ends before it starts")
    if event.get("place") and event["place"] not in places:
        raise ValueError("Event references an unknown place")
    if (event.get("x") is None) != (event.get("y") is None):
        raise ValueError("x and y must both be known or both be null")

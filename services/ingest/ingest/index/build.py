from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Sequence
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from ..time import instant
from ..vault import Record, Vault, read_json, stored
from .timeline import EventTimeline

UNIT_COLUMNS = (
    "day",
    "t",
    "t_end",
    "type",
    "text",
    "place",
    "floor",
    "x",
    "y",
    "evidence_id",
    "source_ref",
    "attention",
    "fidelity",
    "confidence",
    "moment_id",
    "offset_s",
)
INSERT_UNIT = f"INSERT INTO units({','.join(UNIT_COLUMNS)}) VALUES ({','.join('?' for _ in UNIT_COLUMNS)})"

DDL = """
CREATE TABLE units (
  id INTEGER PRIMARY KEY, day TEXT NOT NULL, t TEXT NOT NULL, t_end TEXT, type TEXT NOT NULL,
  text TEXT NOT NULL, place TEXT, floor INTEGER, x REAL, y REAL,
  evidence_id TEXT NOT NULL UNIQUE, source_ref TEXT NOT NULL, attention REAL,
  fidelity TEXT NOT NULL, confidence REAL NOT NULL, moment_id TEXT, offset_s REAL
);
CREATE VIRTUAL TABLE units_fts USING fts5(text, content='units', content_rowid='id');
CREATE TABLE speakers (moment_id TEXT, diar_label TEXT, person_id TEXT, confirmed INTEGER);
CREATE TABLE deletions (t0 TEXT, t1 TEXT, reason TEXT, applied_at TEXT);
"""


def source_stamp(path: Path) -> dict[str, list[int]]:
    """Mtime, size and inode of every file the index is built from; a mismatch forces a rebuild."""
    files = [path / "day.json", path / "events.jsonl", path / "screens/ocr.jsonl"]
    files += sorted((path / "moments").glob("*/meta.json"))
    files += sorted((path / "moments").glob("*/transcript.json"))
    stamp = {}
    for file in files:
        source = stored(file)  # a compacted log counts as the same input under a new stamp
        if source.exists():
            stat = source.stat()
            stamp[str(file.relative_to(path))] = [stat.st_mtime_ns, stat.st_size, stat.st_ino]
    names = path.parent / "places.json"
    if names.exists():
        stat = names.stat()
        stamp["@place_names"] = [stat.st_mtime_ns, stat.st_size, stat.st_ino]
    return stamp


def ocr_runs(shots: Sequence[Record]) -> list[tuple[Record, Record, str]]:
    """Blocks that stay on screen across consecutive shots of one window, one entry per run.

    A screenshot every 30 s repeats most of a static page, so indexing every copy multiplies
    units, full-text postings and embeddings about fivefold for no new text. A run continues while
    the next shot shows the same text under the same title; any gap starts a new run. Each run
    keeps the shot where attention peaked (the first, without gaze) and when it was last seen.
    Returns ``(shot, block, last_seen)`` in order of first appearance.
    """
    runs: list[list] = []
    open_runs: dict[tuple[str, str], list] = {}
    for shot in sorted(shots, key=lambda r: instant(r["t"])):
        seen: dict[tuple[str, str], list] = {}
        for block in shot["blocks"]:
            key = (shot["title"], block["text"])
            run = seen.get(key) or open_runs.get(key)
            if run is None:
                run = [shot, block, shot["t"]]
                runs.append(run)
            else:
                run[2] = shot["t"]
                if _attention(block) > _attention(run[1]):
                    run[0], run[1] = shot, block
            seen[key] = run
        open_runs = seen
    return [(shot, block, last) for shot, block, last in runs]


def _attention(block: Record) -> float:
    return -1.0 if block["attention"] is None else block["attention"]


def collect_units(vault: Vault, day: str) -> list[Record]:
    """Every searchable unit of a day, in a stable order, from a single snapshot parse."""
    snapshot = vault.snapshot(day)
    series = snapshot.events
    moments = snapshot.moments
    units: list[Record] = []
    timeline = EventTimeline.from_series(series)
    for event, start in zip(series.records, series.starts, strict=True):
        moment = moments.get(event["moment_id"]) if event.get("moment_id") else None
        # Store the offset into the moment now, so retrieval never opens meta.json per hit.
        offset = max(0.0, (start - instant(moment.meta["t0"])).total_seconds()) if moment else None
        units.append(
            {
                "day": day,
                "t": event["t"],
                "t_end": event.get("t_end"),
                "type": event["type"],
                "text": f"{event['label']} {event.get('text', '')}".strip(),
                "place": event.get("place"),
                "floor": event.get("floor"),
                "x": event.get("x"),
                "y": event.get("y"),
                "evidence_id": event["id"],
                "source_ref": event["source"]["ref"],
                "attention": event.get("attention"),
                "fidelity": event["fidelity"],
                "confidence": event["confidence"],
                "moment_id": event.get("moment_id"),
                "offset_s": offset,
            }
        )
    for moment in moments.values():
        meta = moment.meta
        start = instant(meta["t0"])
        for index, segment in enumerate(moment.transcript["segments"]):
            evidence = f"transcript:{meta['id']}:{index}"
            units.append(
                {
                    "day": day,
                    "t": (start + timedelta(seconds=segment["start"])).isoformat(),
                    "t_end": (start + timedelta(seconds=segment["end"])).isoformat(),
                    "type": "said.quote",
                    "text": segment["text"],
                    "place": meta["place"],
                    "floor": meta.get("floor"),
                    "x": None,
                    "y": None,
                    "evidence_id": evidence,
                    "source_ref": evidence,
                    "attention": None,
                    "fidelity": meta["fidelity"],
                    "confidence": 1.0 if meta["fidelity"] == "known" else 0.5,
                    "moment_id": meta["id"],
                    "offset_s": segment["start"],
                }
            )
    for record, block, last_seen in ocr_runs(snapshot.ocr):
        if block["attention"] is not None and block["attention"] < 0.3:
            continue
        nearest = timeline.nearest(record["t"])
        evidence = f"ocr:{record['shot']}:{block['id']}"
        units.append(
            {
                "day": day,
                "t": record["t"],
                "t_end": last_seen if last_seen != record["t"] else None,
                "type": "ocr.block",
                "text": f"{record['title']}: {block['text']}",
                "place": nearest.get("place") if nearest else None,
                "floor": nearest.get("floor") if nearest else None,
                "x": None,
                "y": None,
                "evidence_id": evidence,
                "source_ref": f"screens/{record['shot']}.svg",
                "attention": block["attention"],
                "fidelity": nearest["fidelity"] if nearest else "estimated",
                "confidence": block["conf"],
                "moment_id": None,
                "offset_s": None,
            }
        )
    names = vault.place_names()
    for place in snapshot.day["places"]:
        units.append(
            {
                "day": day,
                "t": place["t0"],
                "t_end": place["t1"],
                "type": "place.visit",
                "text": f"Visited {names.get(place['id'], place['name'])}",
                "place": place["id"],
                "floor": None,
                "x": None,
                "y": None,
                "evidence_id": f"place:{place['id']}",
                "source_ref": "day.json",
                "attention": None,
                "fidelity": "estimated",
                "confidence": place["conf"],
                "moment_id": None,
                "offset_s": None,
            }
        )
    return units


def build_index(vault: Vault, day: str) -> Record:
    with vault.lock:
        vault.validate(day)
        path = vault.day_path(day)
        units = collect_units(vault, day)
        descriptor, name = tempfile.mkstemp(prefix=".index-", suffix=".sqlite", dir=path)
        os.close(descriptor)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(temporary)) as db, db:
                db.executescript(DDL)
                db.executemany(INSERT_UNIT, (tuple(unit[column] for column in UNIT_COLUMNS) for unit in units))
                db.execute("INSERT INTO units_fts(units_fts) VALUES ('rebuild')")
            os.replace(temporary, path / "index.sqlite")
            vault.write_json(path / "index.stamp.json", source_stamp(path))
        finally:
            temporary.unlink(missing_ok=True)
        return {"date": day, "units": len(units), "engine": "sqlite-fts5"}


def ensure_index(vault: Vault, day: str) -> None:
    """Rebuild the index if any input changed. A repeat call with unchanged inputs costs stats."""
    path = vault.day_path(day)
    key = (vault.snapshot_key(day), vault.stamp(vault.root / "places.json"), vault.stamp(path / "index.sqlite"))
    if vault.fresh_index.get(day) == key:
        return
    stamp = path / "index.stamp.json"
    if not (path / "index.sqlite").exists() or not stamp.exists() or read_json(stamp) != source_stamp(path):
        build_index(vault, day)
        key = (key[0], key[1], vault.stamp(path / "index.sqlite"))
    vault.fresh_index[day] = key

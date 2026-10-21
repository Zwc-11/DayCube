import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from ..time import instant
from ..vault import Vault, read_json, read_lines
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
INSERT_UNIT = (
    f"INSERT INTO units({','.join(UNIT_COLUMNS)}) VALUES ({','.join('?' for _ in UNIT_COLUMNS)})"
)

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


def source_stamp(path: Path) -> dict:
    files = [path / "day.json", path / "events.jsonl", path / "screens/ocr.jsonl"]
    files += sorted((path / "moments").glob("*/meta.json"))
    files += sorted((path / "moments").glob("*/transcript.json"))
    stamp = {
        str(p.relative_to(path)): [p.stat().st_mtime_ns, p.stat().st_size]
        for p in files
        if p.exists()
    }
    names = path.parent / "places.json"
    if names.exists():
        stamp["@place_names"] = [names.stat().st_mtime_ns, names.stat().st_size]
    return stamp


def collect_units(vault: Vault, day: str) -> list[dict]:
    path = vault.day_path(day)
    units = []
    events = vault.events(day)
    timeline = EventTimeline(events)
    for event in events:
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
                "offset_s": None,
            }
        )
    for meta_path in sorted((path / "moments").glob("*/meta.json")):
        meta = read_json(meta_path)
        start = instant(meta["t0"])
        transcript = read_json(meta_path.parent / "transcript.json")
        for index, segment in enumerate(transcript["segments"]):
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
    for record in read_lines(path / "screens/ocr.jsonl"):
        nearest = timeline.nearest(record["t"])
        for block in record["blocks"]:
            if block["attention"] is not None and block["attention"] < 0.3:
                continue
            evidence = f"ocr:{record['shot']}:{block['id']}"
            units.append(
                {
                    "day": day,
                    "t": record["t"],
                    "t_end": None,
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
    names = read_json(vault.root / "places.json")
    for place in read_json(path / "day.json")["places"]:
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


def build_index(vault: Vault, day: str) -> dict:
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
                db.executemany(
                    INSERT_UNIT, (tuple(unit[column] for column in UNIT_COLUMNS) for unit in units)
                )
                db.execute("INSERT INTO units_fts(units_fts) VALUES ('rebuild')")
            os.replace(temporary, path / "index.sqlite")
            vault.write_json(path / "index.stamp.json", source_stamp(path))
        finally:
            temporary.unlink(missing_ok=True)
        return {"date": day, "units": len(units), "engine": "sqlite-fts5"}


def ensure_index(vault: Vault, day: str) -> None:
    path = vault.day_path(day)
    stamp = path / "index.stamp.json"
    if (
        not (path / "index.sqlite").exists()
        or not stamp.exists()
        or read_json(stamp) != source_stamp(path)
    ):
        build_index(vault, day)

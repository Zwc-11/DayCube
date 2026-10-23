"""What gets searched: one unit per event, transcript segment, on-screen text run and visit.

The units are written to the vault's database by ``ingest.store``; this module only decides what
they are, from a single parse of the day.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from ..time import instant
from ..vault import Record, Vault, stored
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


def source_stamp(path: Path) -> dict[str, list[int]]:
    """Mtime, size and inode of every file a day's units come from; a mismatch forces a rebuild."""
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

"""Deleting a span of a day: ``DELETE /day/{date}/hours`` and the tray's Delete the last hour.

The span is written to ``deletions.jsonl`` first, so from then on the vault drops any record timed
inside it (``Vault.unforgotten``): a receiver still holding a page open, or an import of the same
recording, cannot bring it back. Then everything it holds leaves the source files:

- the computer's capture: every log in ``capture/<date>`` (pages, blocks, gaze, reads, windows,
  inputs, what you were doing);
- the day: its events, GPS fixes and screen text with its shots, and each recording that overlaps
  the span, whole (audio, video, transcript, and its 3D and speech outputs: a recording cannot be
  cut without breaking its timing); a place visit that starts or ends in the span is cut at its
  edge, one inside it goes, one around it keeps its times;
- the phone's kept sources: sensor rows, hours of sound (the span cut out, the rest kept), and the
  originals in ``inbox/phone/imported`` (sensor rows; a memo that overlaps the span, whole).

What is derived from them (the search database, notes, timeline, review, week) is the caller's to
rebuild (``api.py``). The vault's own copies are gone; a backup made before is not reached.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import shutil
import subprocess
import zipfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .time import instant
from .vault import DELETIONS, Record, Vault, overlaps, read_json, read_lines, record_bounds, stored, text_lines

Span = tuple[datetime, datetime]
DERIVED = ("splat", "mesh", "speech")  # vault/derived/<kind>/<date>/<moment>: made from a recording


def forget(vault: Vault, day: str, span: Span, *, reason: str | None = None, now: datetime | None = None) -> Record:
    """Delete ``span`` (t0, t1) of ``day`` from the vault's sources. Returns the deletion as
    ``deletions.jsonl`` keeps it: the span, when, the reason given, and what went, per kind."""
    t0, t1 = span
    if not t0 < t1:
        raise ValueError("The span must end after it starts")
    removed: Counter[str] = Counter()
    with vault.lock:
        audio_cuts = _audio_overlapping(vault, span)
        if audio_cuts and shutil.which("ffmpeg") is None:
            raise RuntimeError("Cutting the span out of a kept hour of sound needs FFmpeg on the PATH")
        when = (now or datetime.now(UTC)).isoformat(timespec="seconds")
        record = {"t0": t0.isoformat(), "t1": t1.isoformat(), "at": when, "date": day, "reason": reason, "removed": {}}
        vault.append_lines(vault.root / DELETIONS, [record])
        folder = vault.root / "capture" / day
        for name in sorted({p.name.removesuffix(".zst") for p in folder.glob("*.jsonl*")}):
            removed["capture"] += _filter_lines(vault, folder / name, span)  # plain or compacted
        if (vault.day_path(day, must_exist=False) / "day.json").is_file():  # a phone or sample day
            _day_files(vault, day, span, removed)
        _sensor_sources(vault, span, removed)
        _audio_sources(vault, audio_cuts, span, removed)
        _imported(vault, span, removed)
        vault.invalidate_index(day)
        record["removed"] = dict(removed)
        kept = read_lines(vault.root / DELETIONS)
        vault.write_lines(vault.root / DELETIONS, [*kept[:-1], record])  # with what went
    return record


def deletions(vault: Vault, day: str) -> list[Record]:
    """The deletions made from ``day``, oldest first."""
    return [r for r in read_lines(vault.root / DELETIONS) if r.get("date") == day]


def _filter_lines(vault: Vault, path: Path, span: Span) -> int:
    """Rewrite a JSON-lines log without its records in the span; how many went. A line that does
    not parse (an append torn by a crash, never acknowledged) is dropped with the rewrite."""
    if not stored(path).exists():
        return 0
    records = []
    for line in text_lines(path):
        with contextlib.suppress(ValueError):
            records.append(json.loads(line))
    keep = [r for r in records if not _hit(r, span)]
    if len(keep) != len(records):
        vault.write_lines(path, keep)
    return len(records) - len(keep)


def _hit(record: Record, span: Span) -> bool:
    bounds = record_bounds(record)
    return bounds is not None and overlaps(*bounds, span)


def _day_files(vault: Vault, day: str, span: Span, removed: Counter[str]) -> None:
    path = vault.day_path(day)
    gone: set[str] = set()  # ids of the events and recordings that went
    for moment in sorted((path / "moments").glob("*/meta.json")):
        meta = read_json(moment)
        start = instant(meta["t0"])
        if overlaps(start, start + timedelta(seconds=float(meta.get("duration_s") or 0)), span):
            gone.add(meta["id"])
            shutil.rmtree(moment.parent)
            for kind in DERIVED:
                shutil.rmtree(vault.root / "derived" / kind / day / meta["id"], ignore_errors=True)
    removed["recordings"] += len(gone)
    events = read_lines(path / "events.jsonl")
    keep = [e for e in events if not _hit(e, span) and e.get("moment_id") not in gone]
    gone |= {e["id"] for e in events} - {e["id"] for e in keep}
    removed["events"] += len(events) - len(keep)
    ocr = path / "screens" / "ocr.jsonl"
    shots = [r for r in read_lines(ocr) if _hit(r, span)]
    for shot in shots:
        if shot.get("shot"):
            (path / "screens" / shot["shot"]).unlink(missing_ok=True)
    removed["screens"] += _filter_lines(vault, ocr, span) if shots else 0
    data = read_json(path / "day.json")
    places, dropped = [], set()
    for place in data["places"]:
        a, b = instant(place["t0"]), instant(place["t1"])
        if not overlaps(a, b, span) or (a < span[0] and b > span[1]):
            places.append(place)  # outside the span, or around it: kept as it is
        elif a < span[0]:
            places.append(dict(place, t1=span[0].isoformat()))
        elif b > span[1]:
            places.append(dict(place, t0=span[1].isoformat()))
        else:
            dropped.add(place["id"])
    removed["places"] += len(dropped)
    skeleton = read_json(path / "skeleton.geojson")
    fixes = [f for f in skeleton["features"] if not _hit(f["properties"], span)]
    removed["fixes"] += len(skeleton["features"]) - len(fixes)
    for record in [f["properties"] for f in fixes] + keep:  # references to a visit that went
        if record.get("place") in dropped:
            record["place"] = None
    for moment in (path / "moments").glob("*/meta.json"):
        meta = read_json(moment)
        if meta.get("place") in dropped:
            vault.write_json(moment, dict(meta, place=None))
    vault.write_lines(path / "events.jsonl", keep)
    vault.write_json(path / "skeleton.geojson", dict(skeleton, features=fixes))
    transits = [
        t for t in data["transits"]
        if not overlaps(instant(t["t0"]), instant(t["t1"]), span) and not {t["from"], t["to"]} & dropped
    ]  # fmt: skip
    faces = [f for f in data["cube_faces"] if f not in gone]
    vault.write_json(path / "day.json", dict(data, places=places, transits=transits, cube_faces=faces))
    graph_path = path / "graph.json"
    if stored(graph_path).exists():
        vault.write_json(graph_path, _graph_without(read_json(graph_path), gone | {f"place:{p}" for p in dropped}))
    vault.refresh_stats(day)


def _graph_without(graph: Record, gone: set[str]) -> Record:
    """The day's graph less the nodes that went, the edges that touched them, and the evidence
    ("ev_009", "transcript:mo_001:0") that named them; an edge left without evidence goes too."""
    nodes = [n for n in graph["nodes"] if n["id"] not in gone]
    ids = {n["id"] for n in nodes}
    edges = []
    for edge in graph["edges"]:
        if edge["from"] not in ids or edge["to"] not in ids:
            continue
        evidence = [e for e in edge.get("evidence", []) if not set(str(e).split(":")) & gone]
        if evidence or not edge.get("evidence"):
            edges.append(dict(edge, evidence=evidence) if "evidence" in edge else edge)
    return dict(graph, nodes=nodes, edges=edges)


def _cut_csv(text: str, span: Span) -> tuple[str, int]:
    """A Sensor Logger CSV without its rows in the span (``time``: epoch nanoseconds)."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or "time" not in rows[0]:
        return text, 0
    at = rows[0].index("time")
    lo, hi = span[0].timestamp() * 1e9, span[1].timestamp() * 1e9
    keep = [rows[0]] + [r for r in rows[1:] if not (len(r) > at and r[at] and lo <= float(r[at]) < hi)]
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(keep)
    return out.getvalue(), len(rows) - len(keep)


def _sensor_sources(vault: Vault, span: Span, removed: Counter[str]) -> None:
    from .phone import sources

    for folder in sources.sensors_between(vault, span[0].timestamp(), span[1].timestamp()):
        for path in folder.glob("*.csv"):
            text, cut = _cut_csv(path.read_text(encoding="utf-8"), span)
            if cut:
                path.write_text(text, encoding="utf-8")
                removed["sensor rows"] += cut


def _audio_overlapping(vault: Vault, span: Span) -> list[tuple[str, Record]]:
    index = vault.root / "sources" / "audio" / "index.json"
    entries = read_json(index) if index.is_file() else {}
    lo, hi = span[0].timestamp(), span[1].timestamp()
    return [(path, info) for path, info in sorted(entries.items()) if info["t0"] < hi and info["t1"] >= lo]


def _audio_sources(vault: Vault, cuts: list[tuple[str, Record]], span: Span, removed: Counter[str]) -> None:
    """Each kept hour of sound loses the span: what is before it stays under its name, what is after
    becomes a file named by its own start, searched for conversations unless the hour already was."""
    if not cuts:
        return
    from .jobs.stages import enqueue
    from .jobs.store import Jobs

    index_path = vault.root / "sources" / "audio" / "index.json"
    index = read_json(index_path)
    lo, hi = span[0].timestamp(), span[1].timestamp()
    jobs = Jobs.of(vault.root)
    try:
        for relative, info in cuts:
            source, t0, t1 = vault.root / relative, info["t0"], info["t1"]
            hour = {"audio": relative, "t0": _utc(t0), "duration_s": round(t1 - t0, 3)}
            searched = any(job["status"] == "done" for job in jobs.find("conversations", hour))
            parts = []
            for a, b in ((t0, min(t1, lo)), (max(t0, hi), t1)):
                if b - a > 1.0:
                    target = source if a == t0 else source.with_name(_stamp(a) + source.suffix)
                    part = target.with_name(target.stem + ".part" + target.suffix)
                    subprocess.run(
                        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{a - t0:.3f}", "-t", f"{b - a:.3f}",
                         "-i", str(source), "-c", "copy", "-map_metadata", "-1", str(part)],
                        check=True, capture_output=True, timeout=600,
                    )  # fmt: skip
                    parts.append((part, target, a, b))
            del index[relative]
            if not any(target == source for _, target, _, _ in parts):
                source.unlink(missing_ok=True)
            for part, target, a, b in parts:
                kept = part.replace(target).relative_to(vault.root).as_posix()
                index[kept] = dict(info, t0=a, t1=b)
                if target != source and not searched:
                    enqueue(jobs, "conversations", {"audio": kept, "t0": _utc(a), "duration_s": round(b - a, 3)})
            removed["hours of sound"] += 1
    finally:
        jobs.close()
    vault.write_json(index_path, index)


def _utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y%m%dT%H%M%SZ")


def _imported(vault: Vault, span: Span, removed: Counter[str]) -> None:
    """The phone's own files as imported (``inbox/phone/imported``): sensor rows in the span go
    (in a folder or inside its zip), and a memo that overlaps the span goes whole."""
    from .phone import voice

    root = vault.root / "inbox" / "phone" / "imported"
    for path in sorted(root.glob("*/*")) if root.is_dir() else []:
        if path.suffix.lower() in voice.AUDIO_SUFFIXES:
            try:
                audio = voice.read(path)
            except (OSError, ValueError):
                continue
            if overlaps(audio.start, audio.start + timedelta(seconds=audio.duration_s), span):
                path.unlink()
                path.with_suffix(".json").unlink(missing_ok=True)
                removed["phone originals"] += 1
        elif path.is_dir():
            for csv_path in path.glob("*.csv"):
                text, cut = _cut_csv(csv_path.read_text(encoding="utf-8"), span)
                if cut:
                    csv_path.write_text(text, encoding="utf-8")
                    removed["sensor rows"] += cut
        elif path.suffix.lower() == ".zip" and zipfile.is_zipfile(path):
            cut = _cut_zip(path, span)
            removed["sensor rows"] += cut


def _cut_zip(path: Path, span: Span) -> int:
    with zipfile.ZipFile(path) as archive:
        members = [(info, archive.read(info)) for info in archive.infolist()]
    total = 0
    out = []
    for info, data in members:
        if info.filename.lower().endswith(".csv"):
            text, cut = _cut_csv(data.decode("utf-8"), span)
            total += cut
            data = text.encode("utf-8") if cut else data
        out.append((info, data))
    if total:
        part = path.with_name(path.name + ".part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as archive:
            for info, data in out:
                archive.writestr(info, data)
        part.replace(path)
    return total

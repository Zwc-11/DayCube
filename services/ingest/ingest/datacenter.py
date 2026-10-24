"""The data center: everything the vault holds, where, and how big, in one report.

The vault's files are the source of truth (day folders, computer capture logs, the notes folder,
calibration); the database (``daycube.sqlite``) is an index built from them and can be deleted
and rebuilt. The report lists both, so it is clear what is original and what is derived.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .capture.synthesize import LOGS
from .store import LifeStore
from .vault import Record


def _size(paths: list[Path]) -> tuple[int, int, float | None]:
    """(files, bytes, newest modification as epoch seconds)."""
    files = size = 0
    newest = None
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        files += 1
        size += stat.st_size
        newest = max(newest or 0.0, stat.st_mtime)
    return files, size, newest


def _iso(epoch: float | None) -> str | None:
    return None if epoch is None else datetime.fromtimestamp(epoch, UTC).isoformat()


def report(store: LifeStore) -> Record:
    root = store.vault.root
    store.ensure()
    sources: list[Record] = []
    day_files = [p for p in root.glob("????-??-??/**/*") if p.is_file()]
    files, size, newest = _size(day_files)
    sources.append({
        "name": "Vault days", "path": "<date>/", "kind": "source", "files": files, "bytes": size,
        "days": len(store.vault.list_days()), "updated": _iso(newest),
        "holds": "places, events, fixes, on-screen text, moments (day.json, events.jsonl, skeleton.geojson, screens/, moments/)",
    })  # fmt: skip
    capture = root / "capture"
    by_log: dict[str, list[Path]] = defaultdict(list)
    days = set()
    if capture.is_dir():
        for path in capture.glob("????-??-??/*"):
            name = path.name.removesuffix(".zst")
            if path.is_file() and name.endswith(".jsonl"):
                by_log[name].append(path)
                days.add(path.parent.name)
    what = {
        "browser.jsonl": "pages, focus, searches, clicks, copies, media",
        "blocks.jsonl": "the text on screen, with lines (web pages, and app windows read from the screen)",
        "gaze.jsonl": "fixations placed on blocks and lines",
        "attention.jsonl": "dwells",
        "reading.jsonl": "line-by-line reads",
        "health.jsonl": "tracker state every 10 minutes",
        "windows.jsonl": "the app in front, and when you were away",
    }
    for name in [*LOGS, *sorted(set(by_log) - set(LOGS))]:
        files, size, newest = _size(by_log.get(name, []))
        sources.append({
            "name": f"Computer capture: {name}", "path": f"capture/<date>/{name}", "kind": "source", "files": files,
            "bytes": size, "days": len({p.parent.name for p in by_log.get(name, [])}), "updated": _iso(newest),
            "holds": what.get(name, ""),
        })  # fmt: skip
    notes = [p for p in store.notes_root.rglob("*.md")] if store.notes_root.is_dir() else []
    files, size, newest = _size(notes)
    sources.append({
        "name": "Notes", "path": "notes/", "kind": "source", "files": files, "bytes": size, "days": None,
        "updated": _iso(newest), "holds": "daily, source and course notes (Obsidian), and your own writing",
    })  # fmt: skip
    calibration = (
        [p for p in (root / "calibration").glob("*") if p.is_file()] if (root / "calibration").is_dir() else []
    )
    files, size, newest = _size(calibration)
    sources.append({
        "name": "Eye tracker calibration", "path": "calibration/", "kind": "source", "files": files, "bytes": size,
        "days": None, "updated": _iso(newest), "holds": "the gaze model and its calibration frames (no images)",
    })  # fmt: skip
    database = [p for p in root.glob(store.path.name + "*") if p.is_file()]
    files, size, newest = _size(database)
    tables = store.tables()
    return {
        "vault": str(root),
        "sources": sources,
        "database": {
            "name": "Vault database",
            "path": store.path.name,
            "kind": "derived",
            "bytes": size,
            "updated": _iso(newest),
            "engine": "SQLite (WAL), FTS5 keyword index, int8 vectors, knowledge graph",
            "embedding_model": store.embedder.name if store.embedder else None,
            **tables,
        },  # fmt: skip
        "capture_days": sorted(days, reverse=True)[:60],
        "totals": {
            "source_bytes": sum(s["bytes"] for s in sources),
            "derived_bytes": size,
        },
    }

"""Synthetic note generation and warm day-view benchmark; never reads the user's vault."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import platform
import pstats
import random
import statistics
import subprocess
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ingest.notes.read import DayNotes
from ingest.notes.write import Notes
from ingest.store import LifeStore
from ingest.vault import Vault
from search_latency import write_day

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-07-01"
SOURCES = (
    "services/ingest/ingest/notes/read.py",
    "services/ingest/ingest/notes/write.py",
    "services/ingest/ingest/store/db.py",
    "services/ingest/ingest/store/notes.py",
    "services/ingest/ingest/store/graph.py",
    "services/ingest/ingest/cache.py",
    "eval/benchmark_notes.py",
    "eval/search_latency.py",
)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def measure(work: Callable[[], Any], repeats: int) -> dict:
    expected = fingerprint(work())
    wall, cpu = [], []
    for _ in range(repeats):
        start, cpu_start = time.perf_counter(), time.process_time()
        value = work()
        cpu.append((time.process_time() - cpu_start) * 1000)
        wall.append((time.perf_counter() - start) * 1000)
        if fingerprint(value) != expected:
            raise AssertionError("Output changed within a workload")
    tracemalloc.start()
    work()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    profile = cProfile.Profile()
    profile.runcall(work)
    stream = io.StringIO()
    pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(12)
    return {
        "wall_ms": wall,
        "median_ms": statistics.median(wall),
        "p95_ms": sorted(wall)[min(repeats - 1, int(0.95 * repeats))],
        "median_cpu_ms": statistics.median(cpu),
        "traced_peak_bytes": peak,
        "sha256": expected,
        "profile": stream.getvalue(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=200)
    parser.add_argument("--notes", type=int, default=5000)
    parser.add_argument("--repeats", type=int, default=11)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    if min(args.pages, args.notes, args.repeats) < 1:
        parser.error("sizes and repeats must be positive")
    with tempfile.TemporaryDirectory() as folder:
        vault = Vault(Path(folder) / "vault")
        vault.initialize()
        lines = [
            "A hash table maps keys to values and resolves collisions by open addressing.",
            "SQLite transactions preserve evidence identity when the derived index changes.",
            "Reading a paragraph produces fixations with explicit confidence and fidelity.",
        ]
        write_day(vault, DAY, random.Random(7), lines, args.pages)
        store = LifeStore(vault)
        store.ensure()
        own = store.notes_root / "Ideas"
        own.mkdir(parents=True)
        for i in range(args.notes):
            (own / f"Idea {i:05d}.md").write_text(f"# Idea {i}\n\nAn unrelated synthetic note.\n", encoding="utf-8")
        store.sync_notes()
        with store.connect() as db:
            page_ids = {r[0] for r in db.execute("SELECT id FROM entities WHERE kind = 'page'")}
            writer = Notes(store)
            statements: list[str] = []
            db.set_trace_callback(statements.append)
            writer._pages(db, page_ids)
            db.set_trace_callback(None)
            pages = measure(lambda: writer._pages(db, page_ids), args.repeats)
            pages["sql_statements"] = len(statements)
        day_notes = DayNotes(store)
        # Setup and first note writes are outside warm-view timing. All subsequent views use
        # the production freshness checks, including directory scans when their interval expires.
        day_notes.view(DAY)
        view = measure(lambda: day_notes.view(DAY), args.repeats)
    report = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu": platform.processor(),
        },
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "workload": {
            "pages": args.pages,
            "unrelated_notes": args.notes,
            "repeats": args.repeats,
            "warmups": 1,
            "seed": 7,
        },
        "operations": {"page_metadata": pages, "warm_day_view": view},
        "limits": "Warm local SQLite/filesystem; tracemalloc is a separate call, not RSS or GPU memory. No HTTP/browser timing. Eleven samples do not estimate production tail latency.",
    }
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"] != report["workload"]:
            raise AssertionError("Workloads differ")
        for name, result in report["operations"].items():
            if result["sha256"] != before["operations"][name]["sha256"]:
                raise AssertionError(f"Output differs: {name}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                name: {k: v for k, v in result.items() if k not in {"profile", "wall_ms"}}
                for name, result in report["operations"].items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

"""Synthetic search workloads: no user vault, camera, network or embedding model."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import platform
import sqlite3
import statistics
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

import numpy as np
from benchmark_core import DAY, WORKLOADS, create_vault, digest
from ingest.ask.retrieve import retrieve
from ingest.config import PROJECT_ROOT
from ingest.store import LifeStore
from ingest.store.vectors import VectorIndex
from ingest.vault import atomic_text


def measure(operation: Callable[[], Any], repeats: int) -> dict:
    expected = digest(operation())
    samples = []
    for _ in range(repeats):
        started = perf_counter()
        result = operation()
        samples.append((perf_counter() - started) * 1000)
        if digest(result) != expected:
            raise RuntimeError("Search result changed across repetitions")
    return {"median_ms": statistics.median(samples), "samples_ms": samples, "output_sha256": expected}


def vector_fixture(db: sqlite3.Connection, days: int, per_day: int) -> bytes:
    db.executescript(
        "CREATE TABLE days (day TEXT PRIMARY KEY, generation INTEGER);"
        "CREATE TABLE units (id INTEGER PRIMARY KEY, day TEXT, ts REAL);"
        "CREATE INDEX scope ON units(day, ts);"
        "CREATE TABLE vectors (id INTEGER PRIMARY KEY, bits BLOB, embedding BLOB);"
    )
    rng = np.random.default_rng(20261023)
    query = b""
    for day in range(days):
        vectors = rng.integers(-127, 128, size=(per_day, 384), dtype=np.int8)
        bits = np.packbits(vectors > 0, axis=1)
        name = f"day-{day:04d}"
        db.execute("INSERT INTO days VALUES (?, ?)", (name, day + 1))
        db.executemany(
            "INSERT INTO units VALUES (?, ?, ?)",
            ((day * per_day + i + 1, name, float(i)) for i in range(per_day)),
        )
        db.executemany(
            "INSERT INTO vectors VALUES (?, ?, ?)",
            ((day * per_day + i + 1, bits[i].tobytes(), vectors[i].tobytes()) for i in range(per_day)),
        )
        if day == days // 2:
            query = vectors[0].tobytes()
    db.commit()
    return query


def retained_bytes(index: VectorIndex) -> int:
    # The old implementation has a plain dict; keeping this adapter lets one workload
    # measure both revisions without changing the timed search or its output fingerprint.
    if hasattr(index, "cache_size_bytes"):
        return index.cache_size_bytes
    return sum(block.ids.nbytes + block.ts.nbytes + block.words.nbytes for block in index._blocks.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    report: dict = {
        "kind": "synthetic-search-warm-filesystem",
        "environment": {"platform": platform.platform(), "python": sys.version, "numpy": np.__version__},
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_sha256": {
            name: hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest()
            for name in (
                "services/ingest/ingest/ask/retrieve.py",
                "services/ingest/ingest/store/vectors.py",
                "services/ingest/ingest/cache.py",
                "eval/benchmark_search.py",
            )
        },
        "workload": {"vector_days": 100, "vectors_per_day": 1000, "dimension": 384, "k": 10, "seed": 20261023},
        "boundaries": "Fixture creation and output hashing excluded; one warmup, then timed repeats. Cold index means a new VectorIndex with a warm SQLite connection.",
        "repeats": args.repeats,
        "metrics": {},
    }
    with TemporaryDirectory(prefix="daycube-search-") as temporary:
        vault = create_vault(Path(temporary) / "vault", WORKLOADS["large"])
        store = LifeStore(vault)
        store.ensure([DAY])

        def operation() -> list:
            return retrieve(store, "Synthetic activity", day=DAY)

        if len(operation()) != 10:
            raise RuntimeError("Expected ten ranked evidence records")
        report["metrics"]["keyword_ten_hits"] = measure(operation, args.repeats)
        profiler = cProfile.Profile()
        profiler.runcall(operation)
        report["retrieval_calls"] = {
            f"{Path(entry.code.co_filename).name}:{entry.code.co_name}": entry.callcount
            for entry in profiler.getstats()
            if not isinstance(entry.code, str) and entry.code.co_name in {"snapshot", "snapshot_key", "place_names"}
        }
    with closing(sqlite3.connect(":memory:")) as db:
        query = vector_fixture(db, 100, 1000)
        cache_bytes = []

        def cold_scope() -> list:
            index = VectorIndex()
            result = index.search(db, query, 10, days=["day-0050"])
            cache_bytes.append(retained_bytes(index))
            return result

        report["metrics"]["vector_cold_one_day"] = measure(cold_scope, args.repeats)
        report["retained_vector_cache_bytes_after_one_day"] = cache_bytes[-1]
        report["metrics"]["vector_cold_all_days"] = measure(lambda: VectorIndex().search(db, query, 10), args.repeats)
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"] != report["workload"]:
            raise RuntimeError("Workloads differ")
        for name, metric in report["metrics"].items():
            previous = before["metrics"][name]
            if previous["output_sha256"] != metric["output_sha256"]:
                raise RuntimeError(f"{name}: search output differs from baseline")
            metric["baseline_median_ms"] = previous["median_ms"]
            metric["speedup"] = previous["median_ms"] / metric["median_ms"]
    atomic_text(args.output, json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "metrics": report["metrics"],
                "retrieval_calls": report["retrieval_calls"],
                "retained_cache_bytes": report["retained_vector_cache_bytes_after_one_day"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

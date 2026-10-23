"""How fast meaning search stays as the vault grows.

Builds a vault database with the real schema (``ingest.store.db.DDL``), filled with random unit
vectors at the spec's rate of about 5,290 units a day, then times the search path the API uses
(``VectorIndex.search``: a sign-bit pass over every unit in scope, then int8 re-scoring of 40x
the wanted count) for 50 neighbours over the whole vault, one day, and the last 7 and 30 days.
Random vectors cost the same to scan as real ones; this measures speed and size, not quality.

    uv run python eval/vector_bench.py --sizes 100000 500000 1930000
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np
from ingest.index.embed import SCALE
from ingest.store.db import DDL, _script
from ingest.store.vectors import VectorIndex, sign_bits

UNITS_PER_DAY = 5290  # docs/storage.md: index units per day after collapsing repeated OCR
DIM = 384
DAY_SECONDS = 86400.0


def vectors(rng: np.random.Generator, count: int) -> np.ndarray:
    raw = rng.standard_normal((count, DIM), dtype=np.float32)
    raw /= np.linalg.norm(raw, axis=1, keepdims=True)
    return np.clip(np.rint(raw * SCALE * 4), -127, 127).astype(np.int8)  # x4: random vectors are flatter


def build(db: sqlite3.Connection, size: int, rng: np.random.Generator) -> float:
    db.execute("BEGIN")
    _script(db, DDL)
    db.execute("COMMIT")
    started = time.perf_counter()
    batch = 20000
    for start in range(0, size, batch):
        block = vectors(rng, min(batch, size - start))
        units, rows = [], []
        for offset, vector in enumerate(block):
            unit = start + offset + 1
            day, second = divmod(unit - 1, UNITS_PER_DAY)
            ts = day * DAY_SECONDS + second
            units.append((unit, f"d{day:05d}", ts, unit.to_bytes(16, "little")))
            raw = vector.tobytes()
            rows.append((unit, sign_bits(raw), raw))
        db.execute("BEGIN")
        db.executemany(
            "INSERT INTO units(id, day, t, type, text, evidence_id, source_ref, fidelity, confidence, ts, text_hash) "
            "VALUES (?, ?, '', 'bench', 'x', ?, '', 'known', 1, ?, ?)",
            ((u, d, str(u), ts, h) for u, d, ts, h in units),
        )
        db.executemany("INSERT INTO vectors VALUES (?, ?, ?)", rows)
        db.execute("COMMIT")
    days = (size - 1) // UNITS_PER_DAY + 1
    db.execute("BEGIN")
    db.executemany(
        "INSERT INTO days VALUES (?, 'UTC', '', ?, 'bench', 0, ?)",
        ((f"d{day:05d}", UNITS_PER_DAY, day + 1) for day in range(days)),
    )
    db.execute("COMMIT")
    return time.perf_counter() - started


def timed(index: VectorIndex, db: sqlite3.Connection, queries: np.ndarray, **scope: object) -> dict:
    samples = []
    for query in queries:
        started = time.perf_counter()
        found = index.search(db, query.tobytes(), 50, **scope)
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    return {
        "median_ms": round(statistics.median(samples), 2),
        "p95_ms": round(ordered[int(0.95 * (len(ordered) - 1))], 2),
        "rows": len(found),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[100000, 500000])
    parser.add_argument("--queries", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("eval/results/vector-bench.json"))
    args = parser.parse_args()
    rng = np.random.default_rng(0)
    report = {
        "kind": "synthetic-random-vectors",
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "cases": [],
    }
    for size in args.sizes:
        with tempfile.TemporaryDirectory(prefix="daycube-vec-") as directory:
            path = Path(directory) / "daycube.sqlite"
            db = sqlite3.connect(path, isolation_level=None)
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = NORMAL")
            seconds = build(db, size, rng)
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            days = (size - 1) // UNITS_PER_DAY + 1
            last = (days - 1) * DAY_SECONDS
            queries = vectors(rng, args.queries)
            index = VectorIndex()
            started = time.perf_counter()
            index.search(db, queries[0].tobytes(), 50)  # first search loads every day's block
            load = time.perf_counter() - started
            vector_bytes = db.execute("SELECT sum(length(bits) + length(embedding)) FROM vectors").fetchone()[0]
            case = {
                "vectors": size,
                "days": days,
                "insert_per_s": round(size / seconds),
                "database_bytes": path.stat().st_size,
                "vector_bytes_per_unit": round(vector_bytes / size),
                "first_search_load_s": round(load, 2),
                "all_days": timed(index, db, queries),
                "one_day": timed(index, db, queries, days=[f"d{days - 1:05d}"]),
                "last_7_days": timed(index, db, queries, since=last - 6 * DAY_SECONDS),
                "last_30_days": timed(index, db, queries, since=last - 29 * DAY_SECONDS),
            }
            db.close()
        report["cases"].append(case)
        print(json.dumps(case), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()

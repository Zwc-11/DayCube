"""Repeatable CPU benchmarks against isolated synthetic vaults; no personal data or network."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sqlite3
import statistics
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from ingest.ask.retrieve import retrieve
from ingest.config import PROJECT_ROOT
from ingest.index.build import build_index, collect_units
from ingest.time import instant
from ingest.vault import Vault, atomic_text, read_json, read_lines

DAY = "2026-10-20"


@dataclass(frozen=True)
class Workload:
    places: int
    events: int
    fixes: int
    screenshots: int


WORKLOADS = {
    "small": Workload(5, 250, 500, 50),
    "large": Workload(50, 5_000, 10_000, 1_000),
}


def create_vault(root: Path, workload: Workload) -> Vault:
    vault = Vault(root)
    vault.initialize()
    path = vault.day_path(DAY, must_exist=False)
    fixture = PROJECT_ROOT / "fixtures/sample-day"
    day = read_json(fixture / "day.json")
    start = instant(day["places"][0]["t0"])
    span = 12 * 3600

    def timestamp(index: int, count: int) -> str:
        return (start + timedelta(seconds=span * index / count)).isoformat()

    day["places"] = [
        dict(
            day["places"][0],
            id=f"place-{index}",
            name=f"Synthetic visit {index}",
            layer_index=index,
            t0=timestamp(index, workload.places),
            t1=timestamp(index + 1, workload.places),
        )
        for index in range(workload.places)
    ]
    day.update(
        transits=[],
        cube_faces=[],
        stats={
            "events": workload.events,
            "moments": 0,
            "places": workload.places,
            "floor_changes": 0,
        },
    )
    vault.write_json(path / "day.json", day)
    template = read_lines(fixture / "events.jsonl")[0]
    events = [
        dict(
            template,
            id=f"event-{index}",
            t=timestamp(index, workload.events),
            place=f"place-{index * workload.places // workload.events}",
            label="needlebenchmark" if index == 0 else f"Synthetic activity {index}",
        )
        for index in range(workload.events)
    ]
    vault.write_lines(path / "events.jsonl", events)
    feature = read_json(fixture / "skeleton.geojson")["features"][0]
    features = [
        dict(
            feature,
            properties=dict(
                feature["properties"],
                t=timestamp(index, workload.fixes),
                place=f"place-{index * workload.places // workload.fixes}",
            ),
        )
        for index in range(workload.fixes)
    ]
    vault.write_json(path / "skeleton.geojson", {"type": "FeatureCollection", "features": features})
    screenshot = read_lines(fixture / "screens/ocr.jsonl")[0]
    vault.write_lines(
        path / "screens/ocr.jsonl",
        [
            dict(screenshot, shot=f"synthetic-{index}", t=timestamp(index, workload.screenshots))
            for index in range(workload.screenshots)
        ],
    )
    vault.write_json(path / "graph.json", {"nodes": [], "edges": []})
    vault.validate(DAY)
    return vault


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def measure(operation: Callable[[], Any], repeats: int) -> dict:
    # Warm the interpreter/filesystem first; hash outside the timed region.
    expected = digest(operation())
    samples = []
    for _ in range(repeats):
        started = perf_counter()
        result = operation()
        samples.append((perf_counter() - started) * 1000)
        if digest(result) != expected:
            raise RuntimeError("Benchmark output changed across repetitions")
    ordered = sorted(samples)
    return {
        "median_ms": statistics.median(samples),
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "samples_ms": samples,
        "output_sha256": expected,
    }


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--case", choices=["all", *WORKLOADS], default="all")
    parser.add_argument(
        "--compare", type=Path, help="Require identical workloads and result hashes"
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    report = {
        "kind": "synthetic-cpu-warm-filesystem",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": sys.version,
            "sqlite": sqlite3.sqlite_version,
            "revision": git_output("rev-parse", "HEAD"),
            "dirty": bool(git_output("status", "--porcelain")),
            "source_sha256": {
                relative: hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest()
                for relative in [
                    "services/ingest/ingest/vault.py",
                    "services/ingest/ingest/index/build.py",
                    "eval/benchmark_core.py",
                    "services/ingest/ingest/index/timeline.py",
                ]
                if (PROJECT_ROOT / relative).is_file()
            },
        },
        "repeats": args.repeats,
        "cases": {},
    }
    for name, workload in WORKLOADS.items():
        if args.case not in {"all", name}:
            continue
        with TemporaryDirectory(prefix="daycube-benchmark-") as temporary:
            vault = create_vault(Path(temporary) / "vault", workload)
            metrics = {}
            for label, operation in [
                ("load_day", lambda: vault.load_day(DAY)),
                ("collect_units", lambda: collect_units(vault, DAY)),
                ("build_index", lambda: build_index(vault, DAY)),
                ("retrieve_warm", lambda: retrieve(vault, DAY, "needlebenchmark")),
            ]:
                metrics[label] = measure(operation, args.repeats)
                print(f"{name}/{label}: {metrics[label]['median_ms']:.2f} ms median", flush=True)
            hits = retrieve(vault, DAY, "needlebenchmark")
            if len(hits) != 1 or hits[0]["id"] != "event-0":
                raise RuntimeError("FTS correctness check failed")
            # Verify the entire stored payload and FTS consistency outside the timing region.
            with closing(sqlite3.connect(vault.day_path(DAY) / "index.sqlite")) as db:
                db.row_factory = sqlite3.Row
                rows = [dict(row) for row in db.execute("SELECT * FROM units ORDER BY id")]
                expected = [
                    dict(unit, id=index)
                    for index, unit in enumerate(collect_units(vault, DAY), start=1)
                ]
                if rows != expected:
                    raise RuntimeError("Stored index differs from collected evidence")
                db.execute("INSERT INTO units_fts(units_fts,rank) VALUES ('integrity-check',1)")
            report["cases"][name] = {
                "workload": asdict(workload),
                "metrics": metrics,
                "verified_index_sha256": digest(rows),
            }
    if args.compare:
        baseline = read_json(args.compare)
        for name, case in report["cases"].items():
            previous = baseline["cases"][name]
            if case["workload"] != previous["workload"]:
                raise RuntimeError(f"{name}: workloads differ")
            for label, metric in case["metrics"].items():
                old = previous["metrics"][label]
                if metric["output_sha256"] != old["output_sha256"]:
                    raise RuntimeError(f"{name}/{label}: output differs from baseline")
                metric["baseline_median_ms"] = old["median_ms"]
                metric["speedup"] = old["median_ms"] / metric["median_ms"]
        report["compared_with"] = str(args.compare)
    atomic_text(args.output, json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()

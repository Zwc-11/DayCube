"""Measure a queued burst of same-day index requests using an isolated synthetic vault."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import sqlite3
import statistics
import subprocess
import sys
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from benchmark_core import DAY, WORKLOADS, create_vault, digest
from fastapi import BackgroundTasks
from ingest.api import IngestRequest, create_app
from ingest.config import PROJECT_ROOT, Settings
from ingest.index.build import build_index
from ingest.vault import Record, Vault, atomic_text, read_json


def sample(vault: Vault, requests: int) -> Record:
    app = create_app(Settings(vault_root=vault.root, _env_file=None))
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    tasks = BackgroundTasks()
    expected = build_index(app.state.vault, DAY)  # Warm snapshot/index outside the measured burst.
    builds = 0

    def counted_build(store: Vault, date: str) -> Record:
        nonlocal builds
        result = build_index(store, date)
        builds += 1
        if result != expected:
            raise RuntimeError("Index build result drifted")
        return result

    async def run_tasks() -> None:
        await asyncio.gather(*(task() for task in tasks.tasks))

    with patch("ingest.api.build_index", counted_build):
        started = perf_counter()
        identifiers = [endpoints["/ingest/{date}"](DAY, IngestRequest(), tasks)["job_id"] for _ in range(requests)]
        asyncio.run(run_tasks())
        elapsed = (perf_counter() - started) * 1000
    states = [endpoints["/jobs/{job_id}"](identifier, False) for identifier in identifiers]
    if any(state["status"] != "complete" or state["result"] != expected for state in states):
        raise RuntimeError("A request did not receive a completed index")
    with closing(sqlite3.connect(vault.day_path(DAY) / "index.sqlite")) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM units ORDER BY id")]
        db.execute("INSERT INTO units_fts(units_fts,rank) VALUES ('integrity-check',1)")
    return {
        "elapsed_ms": elapsed,
        "builds": builds,
        "distinct_job_ids": len(set(identifiers)),
        "index_sha256": digest(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.requests <= 100 or args.repeats < 1:
        parser.error("requests must be 1..100 and repeats must be positive")
    workload = WORKLOADS["large"]
    with TemporaryDirectory(prefix="daycube-job-benchmark-") as temporary:
        vault = create_vault(Path(temporary) / "vault", workload)
        warmup = sample(vault, args.requests)
        samples = [sample(vault, args.requests) for _ in range(args.repeats)]
    if any(row["index_sha256"] != warmup["index_sha256"] for row in samples):
        raise RuntimeError("Index payload changed across repetitions")
    report = {
        "kind": "queued-same-day-index-burst",
        "timestamp": datetime.now(UTC).isoformat(),
        "boundaries": "Direct API handler submission, then concurrent background execution; excludes HTTP, auth, app setup and warmup",
        "workload": asdict(workload),
        "requests": args.requests,
        "repeats": args.repeats,
        "environment": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python": sys.version,
            "sqlite": sqlite3.sqlite_version,
            "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=PROJECT_ROOT).strip(),
            "dirty": bool(
                subprocess.check_output(["git", "status", "--porcelain"], text=True, cwd=PROJECT_ROOT).strip()
            ),
            "source_sha256": {
                path: hashlib.sha256((PROJECT_ROOT / path).read_bytes()).hexdigest()
                for path in [
                    "services/ingest/ingest/api.py",
                    "services/ingest/ingest/jobs.py",
                    "services/ingest/ingest/index/build.py",
                    "services/ingest/ingest/vault.py",
                    "eval/benchmark_core.py",
                    "eval/benchmark_jobs.py",
                ]
                if (PROJECT_ROOT / path).is_file()
            },
        },
        "median_ms": statistics.median(row["elapsed_ms"] for row in samples),
        "samples": samples,
    }
    if args.compare:
        baseline = read_json(args.compare)
        if baseline["workload"] != report["workload"] or baseline["requests"] != args.requests:
            raise RuntimeError("Benchmark workload differs from baseline")
        if any(row["index_sha256"] != baseline["samples"][0]["index_sha256"] for row in samples):
            raise RuntimeError("Stored index payload differs from baseline")
        report["baseline_median_ms"] = baseline["median_ms"]
        report["speedup"] = baseline["median_ms"] / report["median_ms"]
    atomic_text(args.output, json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("median_ms", "samples")}, indent=2))


if __name__ == "__main__":
    main()

"""Retained per-day freshness metadata after a long synthetic vault scan (no disk or user data)."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import time
import tracemalloc
from pathlib import Path

from ingest.vault import Vault

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("services/ingest/ingest/cache.py", "services/ingest/ingest/vault.py", "eval/benchmark_metadata.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=25000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.entries < 100:
        parser.error("at least 100 entries are required")
    vault = Vault(ROOT / "test-results/unused-metadata-vault")  # never initialized or read
    cache = vault.fresh_index
    tracemalloc.start()
    started, cpu_started = time.perf_counter(), time.process_time()
    for i in range(args.entries):
        key = f"synthetic-day-{i:05d}"
        value = (
            tuple((f"moment-{i}-{n}/meta.json", ((n, 2000, i * 10 + n), n)) for n in range(8)),
            None,
            "model-v1",
            (),
        )
        if hasattr(cache, "put_metadata"):
            cache.put_metadata(key, value)
        else:
            cache[key] = value
    elapsed, cpu = time.perf_counter() - started, time.process_time() - cpu_started
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    hot = [cache.get(f"synthetic-day-{i:05d}") for i in range(args.entries - 100, args.entries)]
    if any(value is None for value in hot):
        raise AssertionError("Recent entries were lost")
    report = {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "workload": {"entries": args.entries, "files_per_day_key": 8, "repetitions": 1},
        "retained_entries": len(cache),
        "charged_bytes": getattr(cache, "size", None),
        "capacity_bytes": getattr(cache, "capacity", None),
        "traced_retained_bytes": retained,
        "traced_peak_bytes": peak,
        "population_s": elapsed,
        "population_cpu_s": cpu,
        "recent_output_sha256": hashlib.sha256(json.dumps(hot).encode()).hexdigest(),
        "limits": "One long scan of synthetic per-day keys; no filesystem I/O. Tracemalloc includes population overhead and is not RSS. Eviction intentionally changes the retained set, never source files or derived evidence.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({k: v for k, v in report.items() if k not in {"source_hashes", "environment", "limits"}}, indent=2)
    )


if __name__ == "__main__":
    main()

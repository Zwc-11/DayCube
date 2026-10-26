"""Per-moment job history lookups across synthetic completed jobs; no stage executes."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import tempfile
from pathlib import Path

from benchmark_notes import measure
from ingest.jobs.store import Jobs, job_key

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("services/ingest/ingest/jobs/store.py", "eval/benchmark_job_lookup.py", "eval/benchmark_notes.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    results = {}
    for size in (1000, 50000):
        with tempfile.TemporaryDirectory(prefix="daycube-job-lookup-") as directory:
            jobs = Jobs(Path(directory) / "jobs.sqlite")
            try:
                target = {"date": "2026-10-25", "moment": "target"}
                rows = []
                for i in range(size):
                    inputs = target if i % 10000 == 0 else {"date": "2026-10-25", "moment": f"synthetic-{i}"}
                    rows.append(("speech", i + 1, job_key("speech", i + 1, inputs), json.dumps(inputs, sort_keys=True)))
                # Fixture setup is one transaction and excluded from measurements.
                with jobs._write() as db:
                    db.executemany(
                        "INSERT INTO jobs(stage, version, key, inputs, status, created, updated) VALUES (?, ?, ?, ?, 'done', 0, 0)",
                        rows,
                    )
                jobs._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                result = measure(lambda jobs=jobs, target=target: jobs.find("speech", target), 11)
                result["database_bytes"] = jobs.path.stat().st_size
                result["plan"] = [
                    row[3]
                    for row in jobs._db.execute(
                        "EXPLAIN QUERY PLAN SELECT * FROM jobs WHERE stage = ? AND inputs = ? ORDER BY id DESC",
                        ("speech", json.dumps(target, sort_keys=True)),
                    )
                ]
                results[str(size)] = result
            finally:
                jobs.close()
    report = {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu": platform.processor(),
        },
        "workload": {"rows": [1000, 50000], "repeats": 11, "warmups": 1, "target_every": 10000},
        "operations": results,
        "limits": "Warm SQLite connection/filesystem, synthetic job metadata, in-process lookup only. "
        "No worker, model or network runs. Index setup and fixture insertion excluded. Index storage cost included.",
    }
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"] != report["workload"]:
            raise AssertionError("Workload differs")
        for name, result in results.items():
            if result["sha256"] != before["operations"][name]["sha256"]:
                raise AssertionError(f"Job history differs: {name}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                name: {k: v for k, v in result.items() if k not in {"profile", "wall_ms"}}
                for name, result in results.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

"""Search-to-navigation graph joins at two synthetic day sizes, with output fingerprints."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import platform
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from benchmark_notes import measure
from ingest.capture.synthesize import CaptureDay
from ingest.graph import extract

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-07-01"
SOURCES = ("services/ingest/ingest/graph/extract.py", "eval/benchmark_graph.py", "eval/benchmark_notes.py")


def fixture(pages: int) -> tuple[CaptureDay, list[dict]]:
    capture = CaptureDay(DAY)
    start = datetime(2026, 7, 1, 8, tzinfo=UTC)
    units = []
    for i in range(pages):
        t = (start + timedelta(seconds=i)).isoformat()
        capture.pages.append(
            {
                "id": f"page-{i}",
                "t": t,
                "tab": i % 11,
                "url": f"https://synthetic.invalid/{i}",
                "title": f"Page {i}",
                "transition": "link",
            }
        )
        if i % 5 == 0:
            search = {"id": f"search-{i}", "t": t, "tab": i % 11, "query": f"Question {i}"}
            capture.searches.append(search)
            units.append({"evidence_id": f"capture:search:search-{i}", "type": "web.search"})
    return capture, units


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    results = {}
    for pages in (1000, 10000):
        capture, units = fixture(pages)
        results[str(pages)] = measure(
            lambda units=units, capture=capture: dataclasses.asdict(extract(DAY, units, capture)), 7
        )
    report = {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu": platform.processor(),
        },
        "workload": {"pages": [1000, 10000], "search_every": 5, "tabs": 11, "repeats": 7, "warmups": 1},
        "operations": results,
        "limits": "In-memory synthetic day, includes graph extraction and dataclass conversion. No HTTP, disk or embedding model. CPU timer has coarse Windows resolution; seven samples do not estimate production tail latency.",
    }
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"] != report["workload"]:
            raise AssertionError("Workloads differ")
        for name, result in results.items():
            if result["sha256"] != before["operations"][name]["sha256"]:
                raise AssertionError(f"Graph output differs: {name}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: {
                    n: v
                    for n, v in r.items()
                    if n in {"median_ms", "median_cpu_ms", "p95_ms", "traced_peak_bytes", "sha256"}
                }
                for k, r in results.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

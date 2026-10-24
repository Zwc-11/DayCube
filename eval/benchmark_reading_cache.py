"""Retained line layouts while one synthetic infinite-scroll document grows; no camera or vault."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/capture-win"))

from capture.attention import Attention  # noqa: E402
from capture.reading import LineBox, PageReader  # noqa: E402

SOURCES = (
    "services/capture-win/capture/attention.py",
    "services/capture-win/capture/reading.py",
    "eval/benchmark_reading_cache.py",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocks", type=int, default=20000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.blocks < 100:
        parser.error("at least 100 blocks are required")
    attention = Attention()
    attention.page = PageReader("synthetic-document", 20, 20)
    tracemalloc.start()
    start = time.perf_counter()
    for i in range(args.blocks):
        attention.layout("synthetic-document", str(i), [LineBox(0, i * 40, 400, 20, f"Synthetic paragraph {i}.")])
    population_s = time.perf_counter() - start
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    recent = [attention.page.text((str(i), 0)) for i in range(args.blocks - 100, args.blocks)]
    report = {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "workload": {"blocks": args.blocks, "lines_per_block": 1, "repetitions": 1},
        "layout_entries": len(attention.layouts),
        "reader_entries": len(attention.page.blocks),
        "traced_retained_bytes": retained,
        "traced_peak_bytes": peak,
        "population_s": population_s,
        "recent_output_sha256": hashlib.sha256(json.dumps(recent).encode()).hexdigest(),
        "limits": "Layout admission with tracemalloc enabled, before decoding a fixation. Tests retained layouts, not inference speed, accuracy or process RSS. All text is synthetic.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"source_hashes", "environment", "limits"}}))


if __name__ == "__main__":
    main()

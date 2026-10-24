"""Synthetic history filtering and reading-decoder profiles; no camera or personal vault."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from benchmark_notes import measure
from ingest.vault import Vault

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/capture-win"))

from capture.history import import_history  # noqa: E402
from capture.reading import LineBox, PageReader  # noqa: E402

SOURCES = (
    "services/capture-win/capture/history.py",
    "services/capture-win/capture/reading.py",
    "eval/benchmark_capture_algorithms.py",
    "eval/benchmark_notes.py",
    "services/ingest/ingest/vault.py",
)


def history_workload(root: Path, visits: int, pauses: int, repeats: int) -> dict:
    vault = Vault(root)
    vault.initialize()
    start = datetime(2026, 7, 1, tzinfo=UTC)
    span = 90 * 86400
    records = [
        {
            "id": f"synthetic-{i}",
            "kind": "visit",
            "t": (start + timedelta(seconds=i * span / visits)).isoformat(),
            "url": f"https://synthetic.example/{i % 100}",
            "title": f"Synthetic page {i % 100}",
            "duration_s": 3.0,
            "source": "synthetic",
        }
        for i in range(visits)
    ]
    entries = []
    for i in range(pauses):
        t = start + timedelta(seconds=i * span / pauses)
        entries.extend(
            [
                {"t": t.isoformat(), "paused": True},
                {"t": (t + timedelta(seconds=span / pauses / 4)).isoformat(), "paused": False},
            ]
        )
    vault.append_lines(root / "capture/pauses.jsonl", entries)

    def run() -> dict:
        counts = import_history(vault, ZoneInfo("UTC"), [Path("synthetic-history")])
        output = hashlib.sha256()
        for day in counts:
            output.update(day.encode())
            output.update((root / "capture" / day / "history.jsonl").read_bytes())
        return {"counts": counts, "records_sha256": output.hexdigest()}

    # Isolate import filtering, grouping, sorting and actual atomic file writes. Browser SQLite
    # copying/decoding is deliberately outside this workload; IDs remain stable across runs.
    with patch("capture.history.read_visits", return_value=(records, start.date())):
        return measure(run, repeats)


def reading_workload(columns: int, fixations: int, repeats: int, *, cold: bool) -> dict:
    layout = {
        f"block-{column}-{block}": [
            LineBox(100 + column * 700, 40 + block * 170 + line * 24, 600 if line < 4 else 250, 18, "Synthetic line")
            for line in range(5)
        ]
        for column in range(columns)
        for block in range(6)
    }

    def prepare() -> PageReader:
        result = PageReader("synthetic-document", 20, 40)
        for block, boxes in layout.items():
            result.set_block(block, boxes)
        return result

    rng = random.Random(7)
    boxes = [line for lines in layout.values() for line in lines]
    points = []
    for i in range(fixations):
        line = boxes[(i // 5) % len(boxes)]
        points.append((line.x + (i % 5 + 0.5) * line.w / 5 + rng.gauss(0, 12), line.y + 49 + rng.gauss(0, 12)))
    view = (0.0, 1040.0, float(columns * 700 + 200))
    reader = prepare()
    reader.update(*points[0], 0, view)  # build the warm layout outside timing

    def run() -> list:
        current = prepare() if cold else reader
        current.alpha, current.last = {}, None
        output = []
        for i, (x, y) in enumerate(points):
            posterior = current.update(x, y, i * 0.3, view)
            output.append([list(posterior.items()), current.best(), current.offset()])
        # Check the full offset posterior too, without retaining every internal matrix.
        output.append(list(current.alpha.items()))
        return output

    return measure(run, repeats)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visits", type=int, default=20000)
    parser.add_argument("--pauses", type=int, default=360)
    parser.add_argument("--fixations", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=11)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    if min(args.visits, args.pauses, args.fixations, args.repeats) < 1:
        parser.error("sizes and repeats must be positive")
    with tempfile.TemporaryDirectory() as folder:
        operations = {
            "history_90_days": history_workload(Path(folder) / "vault", args.visits, args.pauses, args.repeats),
            "reading_single_column_warm": reading_workload(1, args.fixations, args.repeats, cold=False),
            "reading_two_columns_warm": reading_workload(2, args.fixations, args.repeats, cold=False),
            "reading_single_column_cold": reading_workload(1, args.fixations, args.repeats, cold=True),
        }
    report = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu": platform.processor(),
        },
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "workload": {
            "visits": args.visits,
            "pauses": args.pauses,
            "days": 90,
            "fixations": args.fixations,
            "reading_lines_per_column": 30,
            "offset_grid": 31,
            "repeats": args.repeats,
            "warmups": 1,
            "seed": 7,
        },
        "operations": operations,
        "limits": "Synthetic CPU and local file writes; no browser SQLite copy, webcam, HTTP or GPU. Cold reading includes layout construction, not interpreter startup. History repeats rewrite the same files. Tracemalloc and cProfile run separately from wall/CPU samples; traced peak is not RSS. Output hashing/retention is included in history timing. Eleven samples do not establish production tail latency or DeepInfer parity.",
    }
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"] != report["workload"] or before["environment"] != report["environment"]:
            raise AssertionError("Workload or environment differs")
        for name, result in operations.items():
            if result["sha256"] != before["operations"][name]["sha256"]:
                raise AssertionError(f"Output differs: {name}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                name: {k: v for k, v in value.items() if k not in {"wall_ms", "profile"}}
                for name, value in operations.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

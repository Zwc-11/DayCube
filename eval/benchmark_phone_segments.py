"""Phone segmentation at day scale on synthetic fixes/activity; no vault, media or model input."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from benchmark_notes import measure
from ingest.phone.sensor_logger import Recording
from ingest.phone.visits import segment
from ingest.skeleton.parsers.types import Fix

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "services/ingest/ingest/phone/visits.py",
    "services/ingest/ingest/phone/sensor_logger.py",
    "services/ingest/ingest/phone/geo.py",
    "services/ingest/ingest/skeleton/parsers/types.py",
    "eval/benchmark_phone_segments.py",
    "eval/benchmark_notes.py",
)


def fixture(hours: int) -> Recording:
    zone = ZoneInfo("America/Toronto")
    start = datetime(2026, 1, 15, tzinfo=zone).timestamp()
    seconds = hours * 3600
    fixes = []
    for k in range(seconds + 1):
        phase, progress = (k // 900) % 4, (k % 900) / 900
        away = 0 if phase == 0 else progress if phase == 1 else 1 if phase == 2 else 1 - progress
        fixes.append(
            Fix(
                datetime.fromtimestamp(start + k, zone),
                45 + away * 0.02,
                -73,
                hacc=5,
                steps=k // 100,
                source="synthetic-segmentation",
            )
        )
    activity = [(start + k, "automotive" if (k // 900) % 2 else "stationary") for k in range(0, seconds + 1, 900)]
    return Recording(
        str(zone),
        "Synthetic day",
        "fixture",
        fixes,
        [(start, 0), (start + seconds, 0)],
        [(start + k, k // 100) for k in range(seconds + 1)],
        activity,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    results = {}
    verified = {}
    for hours in (2, 8, 24):
        recording = fixture(hours)

        def work(recording: Recording = recording) -> list[dict]:
            # Hashing and full Fix serialization are outside the timed workload; the small
            # span summary adds only O(spans) work and checks all derived numeric fields.
            return [
                {**{k: v for k, v in span.__dict__.items() if k != "fixes"}, "fixes": len(span.fixes)}
                for span in segment(recording)
            ]

        results[str(hours)] = measure(work, 7)
        payload = [asdict(span) for span in segment(recording)]
        verified[str(hours)] = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    report = {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu": platform.processor(),
        },
        "workload": {"hours": [2, 8, 24], "fix_hz": 1, "activity_period_s": 900, "repeats": 7, "warmups": 1},
        "operations": results,
        "full_output_sha256": verified,
        "limits": "Warm process; synthetic alternating stays/drives, one fix and step sample per second. "
        "Measures segmentation plus small span summaries, excluding fixture creation, hashing and full Fix serialization. "
        "Full outputs (including source fixes, order and timestamp offsets) are compared outside timing. "
        "No import publication, network, media, model or user data; traced peak is not RSS.",
    }
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"] != report["workload"] or before["full_output_sha256"] != verified:
            raise AssertionError("Workload or full segmentation output differs")
        for key, value in results.items():
            if value["sha256"] != before["operations"][key]["sha256"]:
                raise AssertionError(f"Span summary differs: {key}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {k: {n: v[n] for n in ("median_ms", "median_cpu_ms", "traced_peak_bytes")} for k, v in results.items()}
        )
    )


if __name__ == "__main__":
    main()

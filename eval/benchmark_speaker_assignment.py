"""Synthetic word-to-speaker assignment at recording scale; no audio or models are loaded."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np
from benchmark_notes import measure

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "services/speech/speech/speakers.py",
    "eval/benchmark_speaker_assignment.py",
    "eval/benchmark_notes.py",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "services/speech"))
    from speech.speakers import assign, windows

    results = {}
    sizes = {}
    for seconds in (300, 3600, 10800):
        spans = [(0.0, float(seconds))]
        times = windows(spans)
        labels = np.array([(i // 32) % 3 for i in range(len(times))])
        words = [{"start": i / 3, "end": i / 3 + 0.2} for i in range(seconds * 3)]
        sizes[str(seconds)] = {"words": len(words), "windows": len(times)}
        results[str(seconds)] = measure(
            lambda words=words, spans=spans, times=times, labels=labels: assign(words, spans, times, labels), 7
        )
    report = {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "cpu": platform.processor(),
        },
        "workload": {"recording_seconds": [300, 3600, 10800], "sizes": sizes, "repeats": 7, "warmups": 1},
        "operations": results,
        "limits": "Synthetic continuous speech at three words/s; warm process, no audio, model, GPU, "
        "network or user vault. Measures assignment and its smoothing only, including index setup. "
        "Traced peak is not RSS; sampled tails are not production p95 estimates.",
    }
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"] != report["workload"]:
            raise AssertionError("Workload differs")
        for name, result in results.items():
            if result["sha256"] != before["operations"][name]["sha256"]:
                raise AssertionError(f"Speaker assignments differ: {name}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {k: {n: r[n] for n in ("median_ms", "median_cpu_ms", "traced_peak_bytes")} for k, r in results.items()}
        )
    )


if __name__ == "__main__":
    main()

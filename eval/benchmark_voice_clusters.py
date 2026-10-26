"""Exact voice clustering on synthetic normalized vectors; never reads audio or voiceprints."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import platform
import subprocess
from pathlib import Path

import numpy as np
from benchmark_notes import measure
from ingest.voices import cluster

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("services/ingest/ingest/voices.py", "eval/benchmark_voice_clusters.py", "eval/benchmark_notes.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--sizes", type=int, nargs="+", default=[128, 512, 1024, 1536])
    args = parser.parse_args()
    if min(args.sizes) < 1:
        parser.error("sizes must be positive")
    results = {}
    for size in args.sizes:
        rng = np.random.default_rng(size)
        anchors = np.eye(4, 192, dtype=np.float32)
        prints = anchors[np.arange(size) % 4] + rng.normal(scale=0.02, size=(size, 192)).astype(np.float32)
        prints /= np.linalg.norm(prints, axis=1, keepdims=True)
        recordings = [f"synthetic-{i // 2}" for i in range(size)]
        results[str(size)] = measure(lambda prints=prints, recordings=recordings: cluster(prints, recordings), 7)
    report = {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {
            p: hashlib.sha256(
                (Path(inspect.getfile(cluster)) if p == SOURCES[0] else ROOT / p).read_bytes()
            ).hexdigest()
            for p in SOURCES
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu": platform.processor(),
            "numpy": np.__version__,
            "openblas_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
        },
        "workload": {
            "vectors": args.sizes,
            "dimensions": 192,
            "voices": 4,
            "per_recording": 2,
            "noise": 0.02,
            "repeats": 7,
            "warmups": 1,
        },
        "operations": results,
        "limits": "Synthetic well-separated clusters with cannot-link pairs; includes initial similarity matrix. "
        "Warm process, no model, audio, GPU, vault or network. Output labels must match exactly, including ties. "
        "This is clustering, not recognition or end-to-end job throughput; traced peak is not RSS.",
    }
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"] != report["workload"]:
            raise AssertionError("Workload differs")
        for key, value in results.items():
            if value["sha256"] != before["operations"][key]["sha256"]:
                raise AssertionError(f"Cluster labels differ: {key}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {k: {n: v[n] for n in ("median_ms", "median_cpu_ms", "traced_peak_bytes")} for k, v in results.items()}
        )
    )


if __name__ == "__main__":
    main()

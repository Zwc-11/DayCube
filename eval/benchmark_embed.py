"""Embedding a day's records for meaning search (ingest/index/embed.py): time and peak memory.

Indexing a day embeds every new unit; in the desktop app that runs inside the vault API, which
then keeps whatever memory the embedding reached for as long as it runs. The workload is 2,000
texts whose lengths follow the units of a vault in use (12 days, 18,106 units: median 80
characters, 90th percentile 183, 99th 516, longest 1,200), cut from the docstrings of a few
standard-library modules (fixed for a Python version; no personal text). Each run is a fresh
process, so its peak is its own; ``workload_sha256`` shows two reports measured the same texts.

Also checked: the vectors equal the ones each text gets embedded alone (within one int8 step).

    uv run --project services/capture-win --extra desktop python eval/benchmark_embed.py \
        --output eval/results/embed-after.json --compare eval/results/embed-before.json
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import inspect
import json
import pathlib
import platform
import random
import re
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = (argparse, collections, json, pathlib, subprocess, textwrap, threading)
# Quantiles of unit length (characters) in the vault described above.
QUANTILES = [(0.0, 12), (0.05, 27), (0.1, 30), (0.25, 48), (0.5, 80), (0.75, 125), (0.9, 183), (0.95, 242),
             (0.99, 516), (0.999, 1200), (1.0, 1200)]  # fmt: skip


def workload(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    corpus = " ".join(inspect.getdoc(member) or "" for module in CORPUS for _, member in inspect.getmembers(module)
                      if (inspect.isclass(member) or inspect.isfunction(member))
                      and getattr(member, "__module__", None) == module.__name__)  # fmt: skip
    words = re.findall(r"[A-Za-z][A-Za-z'-]*[.,]?", corpus)
    texts = []
    for _ in range(count):
        q = rng.random()
        (q0, a), (q1, b) = next((lo, hi) for lo, hi in zip(QUANTILES, QUANTILES[1:], strict=False) if q <= hi[0])
        length = round(a + (b - a) * (q - q0) / (q1 - q0))
        start = rng.randrange(len(words) - 300)
        text = ""
        for word in words[start:]:
            if len(text) + len(word) + 1 > length:
                break
            text = f"{text} {word}" if text else word
        texts.append(text)
    return texts


def peak_mb() -> float:
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB on Linux
    except ImportError:  # Windows
        import psutil

        return psutil.Process().memory_info().peak_wset / 2**20


def child(count: int, seed: int) -> dict:
    sys.path.insert(0, str(ROOT / "services/ingest"))
    from ingest.index.embed import FastEmbedder

    texts = workload(count, seed)
    embedder = FastEmbedder()
    embedder.query("warm up")
    loaded = peak_mb()
    started = time.perf_counter()
    vectors = embedder.passages(texts)
    seconds = time.perf_counter() - started
    peak = peak_mb()
    sample = random.Random(seed).sample(range(count), 48)
    alone = [embedder.passages([texts[i]])[0] for i in sample]
    worst = max(abs(a - b) for i, v in zip(sample, alone, strict=True) for a, b in zip(
        memoryview(vectors[i]).cast("b"), memoryview(v).cast("b"), strict=True))  # fmt: skip
    return {"texts": count, "characters": sum(map(len, texts)),
            "workload_sha256": hashlib.sha256("\n".join(texts).encode()).hexdigest(), "seconds": round(seconds, 2),
            "texts_per_s": round(count / seconds, 1), "model_loaded_mb": round(loaded), "peak_mb": round(peak),
            "max_int8_difference_from_alone": worst}  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--texts", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        print(json.dumps(child(args.texts, args.seed)))
        return
    runs = []
    for _ in range(args.repeats):
        done = subprocess.run([sys.executable, __file__, "--child", "--texts", str(args.texts), "--seed", str(args.seed)],
                              capture_output=True, text=True, check=True)  # fmt: skip
        runs.append(json.loads(done.stdout.strip().splitlines()[-1]))
    best = min(runs, key=lambda r: r["seconds"])
    result = {"kind": "embed-day", "environment": {"platform": platform.platform(), "python": platform.python_version(),
              "processor": platform.processor() or platform.machine()}, "best": best, "runs": runs}  # fmt: skip
    if args.compare:
        before = json.loads(args.compare.read_text())["best"]
        result["compared_to"] = {"file": args.compare.name, "speedup": round(before["seconds"] / best["seconds"], 2),
                                 "peak_mb_before": before["peak_mb"], "peak_mb_after": best["peak_mb"]}  # fmt: skip
    text = json.dumps(result, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")


if __name__ == "__main__":
    main()

"""Capture text projection and search under appends, using an isolated synthetic vault.

No camera, user vault, model or network calls. Timing excludes fixture creation and hashing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from benchmark_notes import fingerprint, measure
from ingest.ask.retrieve import retrieve
from ingest.capture import capture_units, read_capture
from ingest.store import LifeStore
from ingest.vault import Vault

ROOT = Path(__file__).resolve().parents[1]
DAY, PREVIOUS = "2026-10-25", "2026-10-24"
SOURCES = (
    "eval/benchmark_capture_projection.py",
    "eval/benchmark_notes.py",
    "services/ingest/ingest/capture/synthesize.py",
    "services/ingest/ingest/vault.py",
    "services/ingest/ingest/cache.py",
    "services/ingest/ingest/store/db.py",
    "services/ingest/ingest/ask/retrieve.py",
)
DOCS, BLOCKS, OLD_SNAPSHOTS, NEW_SNAPSHOTS = 120, 32, 4000, 400


def snapshot(day: str, i: int, text: bool) -> dict:
    doc = i % DOCS
    blocks = [
        {
            "id": f"b{k}",
            "tag": "p",
            "rect": [10, 20 * k, 700, 18],
            **(
                {
                    "text": f"Synthetic topic {doc}-{k}. " * 35,
                    "words": 105,
                    "lines": [{"text": "Synthetic line", "rect": [10, 20 * k, 700, 18]}],
                }
                if text
                else {}
            ),
        }
        for k in range(BLOCKS)
    ]
    return {
        "id": f"blocks-{day}-{i}",
        "t": f"{day}T12:00:00-04:00",
        "doc": f"doc-{doc}",
        "url": f"https://synthetic.invalid/{doc}",
        "title": f"Topic {doc}" if text else "",
        "blocks": blocks,
    }


def fixture(vault: Vault) -> dict[str, int]:
    for day, count in ((PREVIOUS, OLD_SNAPSHOTS), (DAY, NEW_SNAPSHOTS)):
        folder = vault.root / "capture" / day
        folder.mkdir(parents=True)
        with (folder / "blocks.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for i in range(count):
                stream.write(json.dumps(snapshot(day, i, day == PREVIOUS and i < DOCS)) + "\n")
    folder = vault.root / "capture" / DAY
    vault.write_lines(
        folder / "browser.jsonl",
        [
            {
                "id": "search",
                "kind": "search",
                "t": f"{DAY}T12:01:00-04:00",
                "query": "syntheticneedle",
                "engine": "fixture",
            }
        ],
    )
    vault.write_lines(
        folder / "attention.jsonl",
        [
            {
                "id": f"d{i}",
                "kind": "dwell",
                "phase": "end",
                "dwell": f"d{i}",
                "doc": f"doc-{i}",
                "block": "b0",
                "t": f"{DAY}T12:00:{i:02d}-04:00",
                "dwell_s": 5,
                "url": f"https://synthetic.invalid/{i}",
            }
            for i in range(20)
        ],
    )
    return {p.relative_to(vault.root).as_posix(): p.stat().st_size for p in vault.root.glob("capture/*/*.jsonl")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    with tempfile.TemporaryDirectory(prefix="daycube-projection-") as directory:
        vault = Vault(Path(directory) / "vault")
        vault.initialize()
        sizes = fixture(vault)
        store = LifeStore(vault)
        store.ensure()

        def cold() -> list[dict]:
            return capture_units(Vault(vault.root), DAY)

        def warm() -> list[dict]:
            return capture_units(vault, DAY)

        def search_append() -> list[dict]:
            vault.append_lines(vault.root / "capture" / DAY / "blocks.jsonl", [snapshot(DAY, NEW_SNAPSHOTS, False)])
            return [
                {k: v for k, v in row.items() if k != "unit_id"} for row in retrieve(store, "syntheticneedle", day=DAY)
            ]

        operations = {
            "cold_capture": measure(cold, args.repeats),
            "warm_capture": measure(warm, args.repeats),
            "search_after_append": measure(search_append, args.repeats),
        }
        fresh = read_capture(Vault(vault.root), DAY)
        if asdict(read_capture(vault, DAY)) != asdict(fresh):
            raise AssertionError("Cached projection differs from a fresh parse")
        complete = asdict(fresh)
        complete["texts"] = [[list(key), list(value)] for key, value in sorted(fresh.texts.items())]
        verified = fingerprint(complete)
        cache = getattr(vault, "capture_texts", None)
        retained = cache.size if cache is not None else 0
    report = {
        "kind": "synthetic-capture-projection",
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu": platform.processor(),
        },
        "workload": {
            "docs": DOCS,
            "blocks": BLOCKS,
            "snapshots": [OLD_SNAPSHOTS, NEW_SNAPSHOTS],
            "bytes": sizes,
            "repeats": args.repeats,
            "warmups": 1,
        },
        "operations": operations,
        "verified_capture_sha256": verified,
        "cache_charged_bytes": retained,
        "limits": "Warm filesystem, synthetic text/geometry, in-process keyword search. No HTTP, browser or model. "
        "Each search appends one position-only snapshot; output and projection equivalence are checked. "
        "Search hashes omit regenerated SQLite unit_id but retain evidence IDs and all other fields. "
        "Traced peak is not RSS. Small sample tails are maxima, not production p95 estimates.",
    }
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"] != report["workload"] or before["verified_capture_sha256"] != verified:
            raise AssertionError("Workload or complete capture output differs")
        for name, result in operations.items():
            if result["sha256"] != before["operations"][name]["sha256"]:
                raise AssertionError(f"Output differs: {name}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                name: {k: v for k, v in result.items() if k != "profile" and k != "wall_ms"}
                for name, result in operations.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

"""Capture-day indexing and search while capture is running; synthetic logs, never the user's vault.

While the receiver is capturing, today's logs grow every few hundred milliseconds, so every search
finds today stale and indexes it again. This benchmark sizes its synthetic logs like one real
capture day on the development PC (log byte sizes only; no contents were read): about 33 MB of
block snapshots the day before, 5 MB today, plus browser, gaze, reading and dwell logs.

Workloads:

- ``index_capture_day_cold``: a new ``Vault`` and ``LifeStore`` index today from nothing.
- ``search_while_capturing``: append one position-only block snapshot and three fixations, as the
  receiver does, then search across the vault the way ``GET /search`` does.

Both workloads keep the same output across samples (the appended records create no new units).
Outside the timed region the report checks that the indexed units equal a parse from scratch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path

from benchmark_notes import fingerprint, measure
from ingest.ask.retrieve import retrieve
from ingest.capture import capture_units, read_capture
from ingest.index.build import UNIT_COLUMNS
from ingest.store import LifeStore
from ingest.vault import Vault

ROOT = Path(__file__).resolve().parents[1]
TODAY, YESTERDAY = "2026-10-24", "2026-10-23"
NOW = datetime.fromisoformat(f"{TODAY}T15:00:00-04:00")
QUESTION = "word17 word99 site3"
SEED = 20261024
SOURCES = (
    "services/ingest/ingest/capture/synthesize.py",
    "services/ingest/ingest/vault.py",
    "services/ingest/ingest/store/db.py",
    "services/ingest/ingest/graph/extract.py",
    "services/ingest/ingest/cache.py",
    "eval/benchmark_capture_index.py",
    "eval/benchmark_notes.py",
)
GEOMETRY = {
    "vx": 0.0, "vy": 100.0, "scale": 1.0, "inner_w": 1920.0, "inner_h": 980.0, "screen_w": 1920.0,
    "screen_h": 1080.0, "primary": True, "exact": True, "scroll_y": 0.0, "scroll_h": 4000.0,
}  # fmt: skip
SIZES = {
    YESTERDAY: {"blocks_bytes": 33_000_000, "browser": 9_000, "gaze": 14_000, "reads": 300, "dwells": 20},
    TODAY: {"blocks_bytes": 5_000_000, "browser": 1_400, "gaze": 3_300, "reads": 300, "dwells": 20},
}


def _lines(records: list[dict]) -> str:
    return "".join(json.dumps(record) + "\n" for record in records)


def write_capture_day(root: Path, day: str, rng: random.Random) -> None:
    """One synthetic capture day. Each document's first snapshot carries its block texts and lines;
    later snapshots carry positions only, as the extension sends them."""
    size = SIZES[day]
    folder = root / "capture" / day
    folder.mkdir(parents=True, exist_ok=True)
    start = datetime.fromisoformat(f"{day}T08:00:00-04:00")
    words = [f"word{i}" for i in range(4000)]
    docs = [f"doc-{day}-{i}" for i in range(400)]

    def at(seconds: float) -> str:
        return (start + timedelta(seconds=seconds)).isoformat()

    blocks, written, n = [], 0, 0
    while written < size["blocks_bytes"]:
        doc = docs[n % len(docs)]
        first = n < len(docs)
        snapshot = []
        for b in range(40):
            block = {"id": f"b{b}", "tag": "p", "rect": [10.0, 30.0 * b + n % 7, 800.0, 28.0]}
            if first:
                block |= {
                    "text": " ".join(rng.choice(words) for _ in range(60)),
                    "words": 60,
                    "lines": [{"rect": [10.0, 30.0 * b + 14 * k, 790.0, 14.0], "text": "line"} for k in range(3)],
                }
            snapshot.append(block)
        record = {
            "id": f"blk-{day}-{n:08d}", "t": at(n), "kind": "blocks", "tab": 1, "window": 1,
            "url": f"https://site{n % 50}.example/{doc}", "title": f"Title {doc}", "doc": doc,
            "geometry": GEOMETRY, "blocks": snapshot,
        }  # fmt: skip
        line = json.dumps(record) + "\n"
        written += len(line)
        blocks.append(line)
        n += 1
    (folder / "blocks.jsonl").write_text("".join(blocks), encoding="utf-8")

    browser = []
    for i in range(size["browser"]):
        kind = ("page", "focus", "title", "search", "click", "idle")[i % 6]
        record = {
            "id": f"br-{day}-{i:08d}", "t": at(5 * i), "kind": kind, "tab": 1 + i % 5, "window": 1,
            "url": f"https://site{i % 50}.example/{docs[i % len(docs)]}", "title": f"Page {i % 300}",
        }  # fmt: skip
        if kind == "page":
            record |= {"transition": "link" if i % 4 else "typed", "qualifiers": []}
        elif kind == "search":
            record |= {"engine": "google", "query": f"query {rng.choice(words)}"}
        elif kind == "click":
            record |= {"text": "Next", "href": f"https://other{i % 9}.example/"}
        elif kind == "idle":
            record = {"id": record["id"], "t": record["t"], "kind": "idle", "state": "active"}
        browser.append(record)
    (folder / "browser.jsonl").write_text(_lines(browser), encoding="utf-8")

    gaze = []
    for i in range(size["gaze"]):
        t0 = 0.4 * i
        gaze.append({
            "id": f"gz-{day}-{i:08d}", "t": at(t0), "t_end": at(t0 + 0.3), "x": 0.5, "y": 0.5, "samples": 9,
            "doc": docs[i % 60], "block": f"b{i % 40}", "p": 0.8,
        })  # fmt: skip
    (folder / "gaze.jsonl").write_text(_lines(gaze), encoding="utf-8")

    reads = [
        {
            "id": f"rd-{day}-{i:06d}", "t": at(60 * i), "t_end": at(60 * i + 20), "kind": "read",
            "doc": docs[i % len(docs)], "block": f"b{i % 40}", "url": f"https://site{i % 50}.example/",
            "title": f"Title CS {100 + i % 50} notes", "coverage": 0.7, "p": 0.6,
            "lines": [{"text": " ".join(rng.choice(words) for _ in range(9))} for _ in range(4)],
        }  # fmt: skip
        for i in range(size["reads"])
    ]
    (folder / "reading.jsonl").write_text(_lines(reads), encoding="utf-8")

    dwells = []
    for i in range(size["dwells"]):
        base = {"kind": "dwell", "dwell": f"dw-{day}-{i}", "doc": docs[i], "block": "b1", "title": f"Title {i}",
                "url": f"https://site{i}.example/"}  # fmt: skip
        dwells.append({**base, "id": f"dws-{day}-{i:05d}", "t": at(900 * i), "phase": "start"})
        dwells.append({**base, "id": f"dwe-{day}-{i:05d}", "t": at(900 * i + 12), "phase": "end", "dwell_s": 12.0})
    (folder / "attention.jsonl").write_text(_lines(dwells), encoding="utf-8")


def append_live(root: Path, serial: int) -> None:
    """What the receiver appends between two searches: a scroll snapshot and three fixations, on a
    document whose texts have not arrived yet (so no unit changes)."""
    folder = root / "capture" / TODAY
    t = NOW + timedelta(seconds=serial)
    snapshot = {
        "id": f"live-blk-{serial:08d}", "t": t.isoformat(), "kind": "blocks", "tab": 1, "window": 1,
        "url": "https://live.example/", "title": "", "doc": "doc-live", "geometry": GEOMETRY,
        "blocks": [{"id": f"b{b}", "tag": "p", "rect": [10.0, 30.0 * b, 800.0, 28.0]} for b in range(40)],
    }  # fmt: skip
    fixations = [
        {
            "id": f"live-gz-{serial:08d}-{k}", "t": (t + timedelta(seconds=0.3 * k)).isoformat(),
            "t_end": (t + timedelta(seconds=0.3 * k + 0.25)).isoformat(), "x": 0.5, "y": 0.5, "samples": 8,
            "doc": "doc-live", "block": "b3", "p": 0.7,
        }  # fmt: skip
        for k in range(3)
    ]
    with (folder / "blocks.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(_lines([snapshot]))
    with (folder / "gaze.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(_lines(fixations))


def stored_units(store: LifeStore, day: str) -> list[dict]:
    with closing(sqlite3.connect(store.path)) as db:
        db.row_factory = sqlite3.Row
        return [
            {c: row[c] for c in UNIT_COLUMNS}
            for row in db.execute("SELECT * FROM units WHERE day = ? ORDER BY ts, type, evidence_id", (day,))
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=11)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path, help="Require identical workload and output hashes")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    with tempfile.TemporaryDirectory(prefix="daycube-capture-bench-") as folder:
        root = Path(folder) / "vault"
        Vault(root).initialize()
        rng = random.Random(SEED)
        for day in (YESTERDAY, TODAY):
            write_capture_day(root, day, rng)
        log_bytes = {
            path.relative_to(root).as_posix(): path.stat().st_size for path in sorted(root.glob("capture/*/*.jsonl"))
        }

        def index_cold() -> dict:
            return LifeStore(Vault(root)).update(TODAY)

        vault = Vault(root)
        store = LifeStore(vault)
        store.ensure()
        serial = count()

        def search_while_capturing() -> list:
            append_live(root, next(serial))
            return retrieve(store, QUESTION, now=NOW)

        operations = {
            "index_capture_day_cold": measure(index_cold, args.repeats),
            "search_while_capturing": measure(search_while_capturing, args.repeats),
        }
        # Equivalence outside timing: the warm store's rows and capture parse equal a parse from
        # scratch of the same (now longer) logs.
        store.ensure([TODAY])
        fresh = read_capture(Vault(root), TODAY)
        if asdict(read_capture(vault, TODAY)) != asdict(fresh):
            raise AssertionError("Warm capture parse differs from a parse from scratch")
        expected = sorted(
            ({c: unit[c] for c in UNIT_COLUMNS} for unit in capture_units(Vault(root), TODAY, fresh)),
            key=lambda u: (u["t"], u["type"], u["evidence_id"]),
        )
        rows = stored_units(store, TODAY)
        if [r["evidence_id"] for r in rows] != [u["evidence_id"] for u in expected] or {
            json.dumps(r, sort_keys=True) for r in rows
        } != {json.dumps(u, sort_keys=True) for u in expected}:
            raise AssertionError("Indexed capture units differ from a parse from scratch")
        cache = getattr(vault, "logs", None)
        retained = {"capture_log_cache_charged_bytes": cache.size if cache is not None else 0}
    report = {
        "kind": "synthetic-capture-index",
        "environment": {"platform": platform.platform(), "python": platform.python_version(), "cpu": platform.processor()},
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "workload": {"sizes": SIZES, "log_bytes": log_bytes, "seed": SEED, "repeats": args.repeats, "warmups": 1,
                     "question": QUESTION},  # fmt: skip
        "operations": operations,
        "verified_units_sha256": fingerprint(rows),
        "verified_unit_count": len(rows),
        "retained": retained,
        "limits": (
            "Synthetic logs sized like one real day; contents are not representative of real pages. Warm "
            "filesystem, in-process calls (no HTTP), keyword search only (no embedding model). measure() runs "
            "1 warmup + repeats + 1 tracemalloc + 1 cProfile call, so the search workload appends to the logs "
            "on every call and later samples see slightly longer logs. Traced peak is not RSS; the cache charge "
            "is an accounting bound, not process memory."
        ),
    }
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"] != report["workload"]:
            raise AssertionError("Workload differs from the baseline")
        for name, result in operations.items():
            if result["sha256"] != before["operations"][name]["sha256"]:
                raise AssertionError(f"Output differs from the baseline: {name}")
            result["baseline_median_ms"] = before["operations"][name]["median_ms"]
            result["speedup"] = before["operations"][name]["median_ms"] / result["median_ms"]
        if report["verified_units_sha256"] != before["verified_units_sha256"]:
            raise AssertionError("Verified units differ from the baseline")
        report["compared_with"] = str(args.compare)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: {k: v for k, v in value.items() if k not in {"wall_ms", "profile"}} for name, value in operations.items()}, indent=2))  # fmt: skip
    print(json.dumps(retained))


if __name__ == "__main__":
    main()

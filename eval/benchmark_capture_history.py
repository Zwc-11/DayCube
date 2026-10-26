"""Measure indexing synthetic capture-only histories; never reads the user's vault."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path

from benchmark_notes import measure
from ingest.index.build import UNIT_COLUMNS
from ingest.store import LifeStore
from ingest.vault import Vault

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "services/ingest/ingest/store/db.py",
    "services/ingest/ingest/capture/synthesize.py",
    "eval/benchmark_capture_history.py",
    "eval/benchmark_notes.py",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.days, args.repeats) < 1:
        parser.error("days and repeats must be positive")
    with tempfile.TemporaryDirectory() as folder:
        vault = Vault(Path(folder) / "synthetic-vault")
        vault.initialize()
        for i in range(args.days):
            day = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
            path = vault.root / "capture" / day / "browser.jsonl"
            path.parent.mkdir(parents=True)
            record = {
                "id": f"search-{i}",
                "t": f"{day}T10:00:00-04:00",
                "kind": "search",
                "tab": 1,
                "window": 1,
                "url": "https://example.com/",
                "title": "",
                "engine": "example",
                "query": f"synthetic search {i}",
            }
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        def cold_index() -> list[tuple]:
            db_path = vault.root / "benchmark.sqlite"
            db_path.unlink(missing_ok=True)
            store = LifeStore(Vault(vault.root), path=db_path)
            store.ensure()
            with store.connect() as db:
                return [
                    tuple(row)
                    for row in db.execute(
                        f"SELECT {','.join(UNIT_COLUMNS)}, ts, hex(text_hash) FROM units ORDER BY day, evidence_id"
                    )
                ]

        result = measure(cold_index, args.repeats)
    result.update(
        {
            "workload": "cold_capture_history",
            "days": args.days,
            "records_per_day": 1,
            "warmup_runs": 1,
            "samples": args.repeats,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu": platform.processor(),
            "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "profile"}, indent=2))


if __name__ == "__main__":
    main()

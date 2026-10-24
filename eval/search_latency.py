"""How fast search as you type answers, on a vault of synthetic computer capture.

Builds ``--days`` days of capture logs (pages, reads of real English lines taken from the repo's
docs, searches, copies, dwells), indexes them, then times ``GET /search`` through the API for a
fixed set of questions, scoped to one day and across every day, warm (after one untimed run of
each). With ``--vectors`` meaning search runs too (bge-small, the default model).

    uv run python eval/search_latency.py --days 90 --output eval/results/search-latency.json
    uv run --extra vector python eval/search_latency.py --days 30 --vectors --output ...
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from ingest.api import create_app
from ingest.config import PROJECT_ROOT, Settings
from ingest.vault import Vault

QUESTIONS = [
    "hash table",
    "open addressing",
    "what did I read about the vault database",
    "what did I search yesterday",
    "reciprocal rank fusion",
    "gaze calibration error",
    "due friday",
    "what did I copy",
    "sqlite",
    "head movement",
]
SITES = ["en.wikipedia.org", "learn.uwaterloo.ca", "github.com", "stackoverflow.com", "docs.python.org", "arxiv.org"]


def corpus() -> list[str]:
    """Sentences of real English from the repo's documentation."""
    out = []
    for path in sorted((PROJECT_ROOT / "docs").glob("*.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = re.sub(r"[`*_|#>\[\]()]", " ", line).strip(" -")
            if 40 <= len(line) <= 200 and " " in line:
                out.append(" ".join(line.split()))
    return out


def write_day(vault: Vault, day: str, rng: random.Random, lines: list[str], pages_per_day: int) -> None:
    folder = vault.root / "capture" / day
    folder.mkdir(parents=True, exist_ok=True)
    browser, reading, attention = [], [], []
    clock = 8 * 3600

    def t(seconds: float) -> str:
        s = int(seconds)
        return f"{day}T{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}-04:00"

    for n in range(pages_per_day):
        site = rng.choice(SITES)
        title = " ".join(rng.choice(lines).split()[:6])
        url = f"https://{site}/{day}/{n}"
        tab = n % 7 + 1
        if n % 5 == 0:
            query = " ".join(rng.choice(lines).split()[:4]).lower()
            browser.append({"id": f"s-{day}-{n}", "t": t(clock), "kind": "search", "tab": tab, "window": 1,
                            "url": f"https://www.google.com/search?q={n}", "title": f"{query} - Google Search",
                            "engine": "google", "query": query})  # fmt: skip
            clock += 10
        browser.append({"id": f"p-{day}-{n}", "t": t(clock), "kind": "page", "tab": tab, "window": 1, "url": url,
                        "title": title, "transition": "link", "qualifiers": []})  # fmt: skip
        browser.append({"id": f"f-{day}-{n}", "t": t(clock), "kind": "focus", "tab": tab, "window": 1, "url": url,
                        "title": title})  # fmt: skip
        for r in range(2):
            read = rng.sample(lines, 2)
            reading.append({"id": f"r-{day}-{n}-{r}", "kind": "read", "t": t(clock + 20 + r * 30),
                            "t_end": t(clock + 40 + r * 30), "tab": tab, "url": url, "title": title,
                            "doc": f"d-{day}-{n}", "block": f"b{r}", "tag": "p", "line_count": 2,
                            "lines": [{"line": i, "text": text, "seconds": 4.0, "passes": 1} for i, text in enumerate(read)],
                            "coverage": 1.0, "path": [], "offset_px": 0.0, "p": 0.9})  # fmt: skip
        if n % 4 == 0:
            attention.append({"id": f"a-{day}-{n}", "kind": "dwell", "dwell": f"dw-{day}-{n}", "phase": "end",
                              "t": t(clock + 100), "t_end": t(clock + 112), "dwell_s": 12.0, "reading_s": 3.0,
                              "fixations": 30, "tab": tab, "url": url, "title": title, "doc": f"d-{day}-{n}",
                              "block": "b9", "tag": "p", "text": rng.choice(lines)})  # fmt: skip
        if n % 9 == 0:
            browser.append({"id": f"x-{day}-{n}", "t": t(clock + 90), "kind": "selection", "tab": tab, "window": 1,
                            "url": url, "title": title, "text": " ".join(rng.choice(lines).split()[:5]),
                            "copied": True})  # fmt: skip
        clock += 150
    for name, records in (("browser.jsonl", browser), ("reading.jsonl", reading), ("attention.jsonl", attention)):
        (folder / name).write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--pages", type=int, default=80, help="pages per day")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--vectors", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rng = random.Random(7)
    lines = corpus()
    with tempfile.TemporaryDirectory() as tmp:
        vault = Vault(Path(tmp) / "vault")
        vault.initialize()
        first = date(2026, 7, 1)
        days = [(first + timedelta(days=i)).isoformat() for i in range(args.days)]
        for day in days:
            write_day(vault, day, rng, lines, args.pages)
        settings = Settings(vault_root=vault.root, vector_search=args.vectors, _env_file=None)
        with TestClient(create_app(settings)) as client:
            started = time.perf_counter()
            store = client.app.state.store
            store.ensure()
            if args.vectors:
                for day in days:
                    store.update(day)
            index_s = time.perf_counter() - started
            units = store.stats()
            scopes = {"one day": days[-1], "every day": None}
            report: dict = {"days": args.days, "units": units.get("units"), "vectors": args.vectors,
                            "index_s": round(index_s, 1), "questions": len(QUESTIONS), "scopes": {}}  # fmt: skip
            for label, day in scopes.items():
                params = {"date": day} if day else {}
                for q in QUESTIONS:  # one untimed run each: the first loads the vector index
                    client.get("/search", params={"q": q, **params})
                server, wall = [], []
                for _ in range(args.repeats):
                    for q in QUESTIONS:
                        t0 = time.perf_counter()
                        body = client.get("/search", params={"q": q, **params}).json()
                        wall.append((time.perf_counter() - t0) * 1000)
                        server.append(body["took_ms"])
                report["scopes"][label] = {
                    "server_ms": {"p50": round(statistics.median(server), 1), "p95": round(percentile(server, 0.95), 1),
                                  "max": round(max(server), 1)},
                    "request_ms": {"p50": round(statistics.median(wall), 1), "p95": round(percentile(wall, 0.95), 1)},
                }  # fmt: skip
    print(json.dumps(report, indent=2))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

"""How right is the day timeline? Synthetic days scored second by second (ingest/review/simulate.py).

    uv run python eval/day_sim.py                                   # 20 days, clean and noisy
    uv run python eval/day_sim.py --days 50 --seed 7 --output test-results/day-sim.json

Each day is written as the receiver and the phone record it, into its own temporary vault; the
timeline is built from those records and compared with the script. Targets (the plan of
2026-10-30): clean days 100% of seconds right; noisy days at least 98%, with the median boundary
within 2 s. Half the days have phone records (places, walks, a voice memo). The dates run from
2026-10-26, so 2026-10-31 (a 25-hour review day: the clocks go back on 1 November in Toronto) is
among them. Exit code 1 when a target is missed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from ingest.review import build
from ingest.review.simulate import ZONE, score, simulate

FIRST_DAY = date(2026, 10, 26)
CLEAN_TARGET, NOISY_TARGET, BOUNDARY_TARGET_S = 1.0, 0.98, 2.0


def run(days: int, seed: int, noise: bool) -> list[dict]:
    results = []
    with tempfile.TemporaryDirectory() as folder:
        for n in range(days):
            day = (FIRST_DAY + timedelta(days=n)).isoformat()
            scripted, vault = simulate(Path(folder) / f"v{n}", day, seed + n, noise=noise, phone=n % 2 == 0)
            started = time.perf_counter()
            built = build(vault, day, ZONE)
            result = score(scripted, built.segments)
            result["build_ms"] = round((time.perf_counter() - started) * 1000, 1)
            result["segments"] = len(built.segments)
            result["phone"] = n % 2 == 0
            results.append(result)
    return results


def summary(results: list[dict]) -> dict:
    seconds = sum(r["seconds"] for r in results)
    right = sum(r["accuracy"] * r["seconds"] for r in results)
    return {
        "days": len(results),
        "accuracy": right / seconds if seconds else 1.0,
        "worst_day": min(r["accuracy"] for r in results),
        "desk_accuracy": statistics.mean(r["desk_accuracy"] for r in results),
        "median_boundary_s": statistics.median(r["median_boundary_s"] for r in results),
        "worst_p90_boundary_s": max(r["p90_boundary_s"] for r in results),
        "switches": [sum(r["switches_true"] for r in results), sum(r["switches_found"] for r in results)],
        "talk": [sum(r["talk"][0] for r in results), sum(r["talk"][1] for r in results)],
        "build_ms_median": statistics.median(r["build_ms"] for r in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--output", type=Path, help="Write every day's score as JSON")
    args = parser.parse_args()
    report = {}
    for name, noise in (("clean", False), ("noisy", True)):
        results = run(args.days, args.seed, noise)
        report[name] = {"summary": summary(results), "days": results}
        s = report[name]["summary"]
        print(
            f"{name}: {s['accuracy']:.4%} of seconds right (worst day {s['worst_day']:.4%}), "
            f"median boundary {s['median_boundary_s']:.2f} s (worst p90 {s['worst_p90_boundary_s']:.1f} s), "
            f"switches {s['switches'][1]} found of {s['switches'][0]}, talk {s['talk'][0]}/{s['talk'][1]}, "
            f"build {s['build_ms_median']:.0f} ms"
        )
        for r in results:
            if r["accuracy"] < (CLEAN_TARGET if not noise else NOISY_TARGET):
                print(f"  {r['day']}: {r['accuracy']:.4%}; most wrong: {r['wrong'][:3]}")
    clean, noisy = report["clean"]["summary"], report["noisy"]["summary"]
    met = (
        clean["worst_day"] >= CLEAN_TARGET
        and noisy["accuracy"] >= NOISY_TARGET
        and noisy["median_boundary_s"] <= BOUNDARY_TARGET_S
    )
    print("targets met" if met else "targets missed")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"seed": args.seed, **report}, indent=2), encoding="utf-8")
    return 0 if met else 1


if __name__ == "__main__":
    sys.exit(main())

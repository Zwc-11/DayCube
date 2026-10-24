"""Replay synthetic blink artifacts through real blink/tracker code, without a camera or receiver."""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import platform
import pstats
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODULES = ["__init__", "face", "head", "fixations", "track", "receiver", "system"]


def replay() -> dict:
    from gaze.head import Blinks
    from test_blinks import BlinkingEstimator, eyes
    from test_track import run_for, tracker

    def prepared() -> tuple:
        t, scene, receiver, _, clock, _ = tracker()
        estimator = BlinkingEstimator()
        t.estimator, receiver.live = estimator, True
        run_for(t, clock, 2)
        return t, scene, receiver, clock, estimator

    t, scene, receiver, clock, estimator = prepared()
    before = len(receiver.points)
    for aperture, y in [(0.88, 950), (0.5, 1000), (0.03, 1000), (0.03, 1000), (0.7, 950), (0.85, 950), (0.95, 850)]:
        estimator.apertures, scene.point = (0.3 * aperture, 0.3 * aperture), (960.0, float(y))
        t.step()
    contaminated = len(receiver.points) - before
    estimator.apertures, scene.point = (0.3, 0.3), (960.0, 540.0)
    run_for(t, clock, 0.5)
    dip = max(abs(y * 1080 - 540) for _, _, y in receiver.points[before:])

    t, scene, receiver, clock, estimator = prepared()
    before = len(receiver.points)
    estimator.apertures, scene.point = (0.01, 0.01), (960.0, 1000.0)
    run_for(t, clock, 3)
    closed_updates = len(receiver.points) - before

    t, scene, receiver, clock, estimator = prepared()
    before = len(receiver.points)
    scene.point = (960.0, 900.0)
    run_for(t, clock, 0.5)
    healthy = receiver.points[before:]
    trajectory = hashlib.sha256(json.dumps(healthy).encode()).hexdigest()
    arrival = next((stamp - healthy[0][0] for stamp, _, y in healthy if abs(y * 1080 - 900) < 50), None)

    detector, pixels = Blinks(), eyes()
    for _ in range(50):
        detector(pixels)

    def work() -> int:
        return sum(detector(pixels) for _ in range(50000))

    work()  # warmup, outside timing
    wall, cpu = [], []
    for _ in range(7):
        started, cpu_started = time.perf_counter(), time.process_time()
        assert work() == 0
        wall.append((time.perf_counter() - started) * 1000)
        cpu.append((time.process_time() - cpu_started) * 1000)
    tracemalloc.start()
    work()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    profile = cProfile.Profile()
    profile.runcall(work)
    stream = io.StringIO()
    pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(10)
    return {
        "partial_blink_bad_updates": contaminated,
        "partial_blink_peak_vertical_artifact_px": dip,
        "closed_eye_updates_3s": closed_updates,
        "healthy_downward_saccade_sha256": trajectory,
        "healthy_downward_saccade_arrival_s": arrival,
        "classifier": {
            "calls_per_sample": 50000,
            "samples": 7,
            "warmups": 1,
            "wall_ms": wall,
            "median_us_per_call": statistics.median(wall) * 1000 / 50000,
            "median_cpu_ms": statistics.median(cpu),
            "traced_peak_bytes": peak,
            "history_entries": len(detector.history),
            "profile": stream.getvalue(),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", default="2cd4811")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--code", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        sys.path[:0] = [str(args.code or ROOT / "services/gaze"), str(ROOT / "services/gaze/tests")]
        print(json.dumps(replay()))
        return
    if args.output is None:
        parser.error("--output is required")
    paths = [f"services/gaze/gaze/{name}.py" for name in MODULES]
    revision = subprocess.check_output(["git", "rev-parse", args.compare], cwd=ROOT, text=True).strip()
    before_sources = {p: subprocess.check_output(["git", "show", f"{revision}:{p}"], cwd=ROOT) for p in paths}
    with tempfile.TemporaryDirectory(prefix="blink-replay-") as folder:
        package = Path(folder) / "gaze"
        package.mkdir()
        for path, contents in before_sources.items():
            (package / Path(path).name).write_bytes(contents)
        command = [sys.executable, str(Path(__file__).resolve()), "--child"]
        before = json.loads(subprocess.check_output([*command, "--code", folder], cwd=ROOT, text=True))
        after = json.loads(subprocess.check_output(command, cwd=ROOT, text=True))
    if before["healthy_downward_saccade_sha256"] != after["healthy_downward_saccade_sha256"]:
        raise AssertionError("Unobscured eye movement changed")
    common = ["eval/blink_replay.py", "services/gaze/tests/test_blinks.py", "services/gaze/tests/test_track.py"]
    report = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu": platform.processor(),
            "numpy": np.__version__,
        },
        "before_revision": revision,
        "after_base_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "before_source_hashes": {p: hashlib.sha256(data).hexdigest() for p, data in before_sources.items()},
        "after_source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths},
        "common_fixture_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in common},
        "before": before,
        "after": after,
        "limits": "Scripted 30 FPS loop, synthetic eye-aperture landmarks and deliberately corrupted predicted points. No neural landmark inference, camera, recordings or HTTP. Checks blink rejection and trusted-update behavior, not real blink sensitivity/specificity or optical accuracy. Arrival times are scripted time; classifier timings are separate unpaced CPU samples, with separate cProfile/tracemalloc. No end-to-end speed claim; new reopening grace intentionally withholds samples.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                phase: {k: v for k, v in report[phase].items() if k != "classifier"}
                | {"classifier_us": report[phase]["classifier"]["median_us_per_call"]}
                for phase in ("before", "after")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

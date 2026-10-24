"""FIFO versus production latest-frame acquisition with a synthetic 30 FPS source; no webcam."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import queue
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/gaze"))

from gaze.track import LatestFrame  # noqa: E402


class BufferedCamera:
    def __init__(self, fps: float) -> None:
        self.frames: queue.Queue[tuple[int, float]] = queue.Queue(maxsize=256)
        self.stopping = threading.Event()
        self.fps = fps
        self.thread = threading.Thread(target=self.produce, name="synthetic source")
        self.thread.start()

    def produce(self) -> None:
        start, i = time.monotonic(), 0
        while not self.stopping.is_set():
            try:
                self.frames.put((i, time.monotonic()), timeout=0.1)
            except queue.Full:
                continue
            i += 1
            self.stopping.wait(max(0.0, start + i / self.fps - time.monotonic()))

    def read(self) -> tuple[bool, tuple[int, float] | None]:
        try:
            return True, self.frames.get(timeout=0.2)
        except queue.Empty:
            return False, None

    def release(self) -> None:
        self.stopping.set()
        self.thread.join(1)
        if self.thread.is_alive():
            raise RuntimeError("synthetic producer did not stop")


def run(seconds: float, fps: float, processing_ms: float, *, latest: bool) -> dict:
    camera = LatestFrame(lambda: BufferedCamera(fps)) if latest else BufferedCamera(fps)
    ages, skipped, last = [], 0, -1
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            ok, frame = camera.read()
            if not ok:
                continue
            index, captured = frame
            ages.append((time.monotonic() - captured) * 1000)
            if index <= last:
                raise AssertionError("frame repeated or out of order")
            skipped += index - last - 1
            last = index
            # Stand-in for dual landmark detection plus drawing a calibration target.
            time.sleep(processing_ms / 1000)
    finally:
        camera.release()
    if not ages:
        raise AssertionError("no frames processed")
    return {
        "processed": len(ages),
        "skipped": skipped,
        "age_ms": ages,
        "median_age_ms": statistics.median(ages),
        "p95_age_ms": sorted(ages)[min(len(ages) - 1, int(0.95 * len(ages)))],
        "max_age_ms": max(ages),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument("--fps", type=float, default=30)
    parser.add_argument("--processing-ms", type=float, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (
        all(math.isfinite(value) for value in (args.seconds, args.fps, args.processing_ms))
        and 0 < args.seconds <= 5
        and args.fps > 0
        and args.processing_ms > 0
    ):
        parser.error("require 0 < seconds <= 5 and positive FPS/processing time")
    report = {
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in (
                "services/gaze/gaze/track.py",
                "services/gaze/gaze/calibrate.py",
                "eval/profile_camera_freshness.py",
            )
        },
        "workload": {"seconds_per_mode": args.seconds, "source_fps": args.fps, "processing_ms": args.processing_ms},
        "fifo": run(args.seconds, args.fps, args.processing_ms, latest=False),
        "latest": run(args.seconds, args.fps, args.processing_ms, latest=True),
        "limits": "One real-time synthetic overload trial per mode, including OS scheduling. Source frames contain only an index and a monotonic timestamp, not pixels. Demonstrates queue age under overload; does not establish real camera buffering, optical accuracy, neural inference throughput, CPU/memory budgets or production tail latency. Latest-frame mode intentionally skips superseded frames. No network, webcam or recordings.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                mode: {key: value for key, value in report[mode].items() if key != "age_ms"}
                for mode in ("fifo", "latest")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

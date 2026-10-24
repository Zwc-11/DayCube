"""Offline camera preparation and head-aware prediction; all images/faces are synthetic."""

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
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import mediapipe as mp
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/gaze"))
sys.path.insert(0, str(ROOT / "services/gaze/tests"))

from gaze.face import Landmarker  # noqa: E402
from gaze.head import Blinks, GazeModel, HeadEstimator, Poser, pack  # noqa: E402
from test_head import CAMERA, GRID, HOME, MOVED, SCREEN, TESTS, face  # noqa: E402

SOURCES = (
    "services/gaze/gaze/face.py",
    "services/gaze/gaze/head.py",
    "services/gaze/tests/test_head.py",
    "eval/benchmark_gaze_pipeline.py",
)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def measure(work: Callable[[], Any], frames: int, repeats: int) -> dict:
    expected = fingerprint(work())
    wall, cpu = [], []
    for _ in range(repeats):
        started, cpu_started = time.perf_counter(), time.process_time()
        output = work()
        wall.append((time.perf_counter() - started) * 1000)
        cpu.append((time.process_time() - cpu_started) * 1000)
        if fingerprint(output) != expected:
            raise AssertionError("Workload output changed")
    tracemalloc.start()
    work()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    profile = cProfile.Profile()
    profile.runcall(work)
    stream = io.StringIO()
    pstats.Stats(profile, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(15)
    return {
        "wall_ms": wall,
        "median_ms": statistics.median(wall),
        "p95_ms": sorted(wall)[min(repeats - 1, int(0.95 * repeats))],
        "median_cpu_ms": statistics.median(cpu),
        "median_us_per_frame": statistics.median(wall) * 1000 / frames,
        "traced_peak_bytes": peak,
        "sha256": expected,
        "profile": stream.getvalue(),
    }


class Detection:
    def __init__(self, points: np.ndarray) -> None:
        self.result = SimpleNamespace(face_landmarks=[[SimpleNamespace(x=x, y=y, z=z) for x, y, z in points]])
        self.last: mp.Image | None = None

    def detect(self, image: mp.Image) -> object:
        self.last = image
        return self.result


def preparation(size: tuple[int, int], frames: int, repeats: int) -> dict:
    width, height = size
    rng = np.random.default_rng(7)
    # Alternate real pixel arrays, without opening a camera or saving an image.
    images = rng.integers(0, 256, (2, height, width, 3), dtype=np.uint8)
    detector = Detection(rng.random((478, 3)))
    landmarker = object.__new__(Landmarker)
    landmarker._mp, landmarker.video, landmarker._last_ms = mp, False, 0
    landmarker._landmarker, landmarker._rgb = detector, None
    only = Poser(CAMERA).needed

    def run() -> dict:
        for i in range(frames):
            result = landmarker.detect(images[i % 2], only=only)
        return {
            "points": result.points[only].tolist(),
            "rgb_sha256": hashlib.sha256(detector.last.numpy_view().tobytes()).hexdigest(),
        }

    return measure(run, frames, repeats)


def prediction(frames: int, repeats: int) -> dict[str, dict]:
    rng = np.random.default_rng(7)
    poser = Poser(CAMERA)
    calibration = [poser.measure(face(p, *HOME, rng)) for p in GRID for _ in range(3)]
    model = GazeModel(SCREEN).fit(calibration, np.repeat(np.array(GRID, dtype=float), 3, axis=0))
    faces = [face(p, *pose, rng) for pose in [HOME, *MOVED] for p in TESTS]
    X = np.array([pack(poser.measure(f)) for f in faces])
    estimator = HeadEstimator((1920, 1080), 531 / 1920, landmarker=object())
    estimator.model = model

    def predict() -> list:
        return [estimator.predict(X[i % len(X) : i % len(X) + 1]).tolist() for i in range(frames)]

    class Faces:
        index = 0

        def detect(self, frame: object) -> object:
            value = faces[self.index % len(faces)]
            self.index += 1
            return value

    prepared = Faces()
    estimator._landmarker = prepared

    def track() -> list:
        prepared.index, estimator._blinks = 0, Blinks()
        output = []
        for _ in range(frames):
            features, blink = estimator.extract_features(None)
            output.append([features.tolist(), blink, estimator.predict(features[None, :]).tolist()])
        return output

    return {
        "head_prediction": measure(predict, frames, repeats),
        "head_extract_and_predict": measure(track, frames, repeats),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=11)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    if min(args.frames, args.repeats) < 1:
        parser.error("frames and repeats must be positive")
    operations = prediction(args.frames, args.repeats)
    for size in [(640, 480), (1920, 1080)]:
        operations[f"prepare_{size[0]}x{size[1]}"] = preparation(size, args.frames, args.repeats)
    report = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu": platform.processor(),
            "numpy": np.__version__,
            "opencv": cv2.__version__,
            "mediapipe": mp.__version__,
            "opencv_threads": cv2.getNumThreads(),
        },
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
        "workload": {
            "frames": args.frames,
            "repeats": args.repeats,
            "warmups": 1,
            "seed": 7,
            "synthetic_poses": 5,
            "synthetic_targets": 5,
        },
        "operations": operations,
        "limits": "Synthetic BGR images and projected faces. Preparation uses actual cvtColor/MediaPipe Image copies and selective conversion of landmarks but substitutes native face detection. Head extraction/prediction starts from known landmarks: no neural inference, camera/exposure/driver wait, smoothing, transport or rendering. Tracemalloc excludes some native allocations and is not RSS; profiles/memory run separately from timing. No webcam frames are opened, saved or transmitted. These are CPU-stage timings, not actual FPS, live accuracy or DeepInfer parity.",
    }
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"] != report["workload"] or before["environment"] != report["environment"]:
            raise AssertionError("Workload or environment differs")
        for name, result in operations.items():
            if result["sha256"] != before["operations"][name]["sha256"]:
                raise AssertionError(f"Output differs: {name}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                name: {k: v for k, v in result.items() if k not in {"wall_ms", "profile"}}
                for name, result in operations.items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

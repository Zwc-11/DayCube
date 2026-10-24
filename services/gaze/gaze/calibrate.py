"""Calibrate the webcam gaze model: nine targets to learn from, then four held-out targets to
measure how far off it is, then a final model trained on all thirteen.

The model is the head-aware one (``gaze.head``): it learns where the eyes point within the head,
and the head's pose is measured every frame, so the head may move after calibrating. ``--model``
still takes an EyeTrax regression (ridge, ...), which maps the eyes straight to the screen and is
right only while the head stays where it was.

Written to ``vault/calibration/``: ``gaze-head.json`` (or EyeTrax's ``gaze-model.pkl``) and
``gaze.json`` (monitor, camera, held-out error). The error decides whether tracking runs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

MODEL_FILE = "gaze-model.pkl"  # EyeTrax regressions
HEAD_MODEL_FILE = "gaze-head.json"
DEFAULT_MODEL = "head"
CALIBRATION_FILE = "gaze.json"
# Held-out targets sit between the training grid's points, where interpolation is hardest.
HELD_OUT = [(0.3, 0.3), (0.7, 0.3), (0.3, 0.7), (0.7, 0.7)]
TRAIN_ORDER = [(1, 1), (0, 0), (2, 0), (0, 2), (2, 2), (1, 0), (0, 1), (2, 1), (1, 2)]
# Median held-out error above this is too coarse to tell paragraphs apart at 100% zoom. 150 px let
# through a 183 px calibration on a 2560 px monitor (137 px at 1920) that could not single out
# most paragraphs; the recalibration that followed measured 69 px (eval/gaze_sim.py, docs/capture.md).
USABLE_PX_AT_1920 = 120


class Aborted(Exception):
    pass


class Mirrored(Exception):
    """The webcam delivers a mirrored image (some drivers and virtual cameras do)."""


def usable_limit(width: int) -> float:
    return USABLE_PX_AT_1920 * width / 1920


def model_file(model: str) -> str:
    return HEAD_MODEL_FILE if model == "head" else MODEL_FILE


def estimator_for(model: str, monitor: tuple[int, int], mm_per_px: float | None, landmarker: object = None) -> object:
    """A gaze estimator with EyeTrax's interface: the head-aware model or an EyeTrax regression."""
    if model == "head":
        from .head import HeadEstimator
        from .system import DEFAULT_MM_PER_PX

        return HeadEstimator(monitor, mm_per_px or DEFAULT_MM_PER_PX, landmarker=landmarker)
    from eyetrax import GazeEstimator

    return GazeEstimator(model_name=model)


def calibrate(vault: Path, camera: int = 0, model: str = DEFAULT_MODEL) -> dict:
    import cv2
    from eyetrax.calibration.common import _pulse_and_capture, compute_grid_points, wait_for_face_and_countdown
    from eyetrax.utils.video import open_camera

    from .system import monitor_mm_per_px, primary_monitor

    width, height = primary_monitor()
    pitch, source = monitor_mm_per_px()
    estimator = estimator_for(model, (width, height), pitch)
    try:
        for attempt in range(2):
            capture = open_camera(camera)
            try:
                if not wait_for_face_and_countdown(capture, estimator, width, height, 2):
                    raise Aborted
                points = compute_grid_points(TRAIN_ORDER, width, height)
                train = _pulse_and_capture(estimator, capture, points, width, height)
                if train is None:
                    raise Aborted
                targets = [(int(width * x), int(height * y)) for x, y in HELD_OUT]
                held = _pulse_and_capture(estimator, capture, targets, width, height)
                if held is None:
                    raise Aborted
            finally:
                capture.release()
                cv2.destroyAllWindows()
            try:
                return fit(estimator, vault, (width, height), camera, model, train, held, pitch=(pitch, source))
            except Mirrored:
                if attempt:
                    raise RuntimeError(
                        "The webcam image looks mirrored either way: is the right camera selected?"
                    ) from None
                print("The webcam image is mirrored. Once more, please: it will be flipped back this time.")
                estimator.mirror = True
        raise AssertionError("unreachable")
    finally:
        close = getattr(estimator, "close", None)
        if close is not None:
            close()


def fit(
    estimator: object,
    vault: Path,
    monitor: tuple[int, int],
    camera: int,
    model: str,
    train: tuple,
    held: tuple,
    pitch: tuple[float, str] | None = None,
) -> dict:
    """Train on the nine targets, measure on the four held out, then train on all and save."""
    import numpy as np

    (features, points), (held_features, held_points) = train, held
    if len(features) < 30 or len(held_features) < 8:
        raise RuntimeError("Too few frames with a face. Sit facing the camera in even light and try again.")
    mirrored = getattr(estimator, "mirrored", None)
    if mirrored is not None and mirrored(np.array(features), np.array(points)):
        raise Mirrored
    estimator.train(np.array(features), np.array(points))
    errors = np.linalg.norm(estimator.predict(np.array(held_features)) - np.array(held_points), axis=1)
    # The reported error is the held-out one; the saved model also learns from those targets.
    estimator.train(np.array(list(features) + list(held_features)), np.array(list(points) + list(held_points)))
    folder = vault / "calibration"
    folder.mkdir(parents=True, exist_ok=True)
    estimator.save_model(folder / model_file(model))
    error = float(np.median(errors))
    result = {
        "t": datetime.now(UTC).isoformat(),
        "monitor": list(monitor),
        "camera": camera,
        "model": model,
        "model_file": model_file(model),
        "error_px": round(error, 1),
        "error_p90_px": round(float(np.percentile(errors, 90)), 1),
        "usable": error <= usable_limit(monitor[0]),
        "frames": {"train": len(features), "held_out": len(held_features)},
    }
    if pitch is not None:
        result["mm_per_px"], result["mm_per_px_source"] = round(pitch[0], 4), pitch[1]
    if getattr(estimator, "mirror", False):
        result["mirrored_camera"] = True
    (folder / CALIBRATION_FILE).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def load(vault: Path) -> dict | None:
    path = vault / "calibration" / CALIBRATION_FILE
    if not path.is_file():
        return None
    result = json.loads(path.read_text(encoding="utf-8"))
    if not (vault / "calibration" / result.get("model_file", MODEL_FILE)).is_file():
        return None
    return result

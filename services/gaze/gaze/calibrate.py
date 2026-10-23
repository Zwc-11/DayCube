"""Calibrate the webcam gaze model: nine targets to learn from, then four held-out targets to
measure how far off it is, then a final model trained on all thirteen.

Written to ``vault/calibration/``: ``gaze-model.pkl`` (EyeTrax's regression model) and
``gaze.json`` (monitor, camera, held-out error). The error decides whether tracking runs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

MODEL_FILE = "gaze-model.pkl"
CALIBRATION_FILE = "gaze.json"
# Held-out targets sit between the training grid's points, where interpolation is hardest.
HELD_OUT = [(0.3, 0.3), (0.7, 0.3), (0.3, 0.7), (0.7, 0.7)]
TRAIN_ORDER = [(1, 1), (0, 0), (2, 0), (0, 2), (2, 2), (1, 0), (0, 1), (2, 1), (1, 2)]
USABLE_PX_AT_1920 = 150  # median held-out error above this is too coarse to tell paragraphs apart


class Aborted(Exception):
    pass


def usable_limit(width: int) -> float:
    return USABLE_PX_AT_1920 * width / 1920


def calibrate(vault: Path, camera: int = 0, model: str = "ridge") -> dict:
    import cv2
    from eyetrax import GazeEstimator
    from eyetrax.calibration.common import _pulse_and_capture, compute_grid_points, wait_for_face_and_countdown
    from eyetrax.utils.video import open_camera

    from .system import primary_monitor

    width, height = primary_monitor()
    estimator = GazeEstimator(model_name=model)
    capture = open_camera(camera)
    try:
        if not wait_for_face_and_countdown(capture, estimator, width, height, 2):
            raise Aborted
        train = _pulse_and_capture(estimator, capture, compute_grid_points(TRAIN_ORDER, width, height), width, height)
        if train is None:
            raise Aborted
        targets = [(int(width * x), int(height * y)) for x, y in HELD_OUT]
        held = _pulse_and_capture(estimator, capture, targets, width, height)
        if held is None:
            raise Aborted
    finally:
        capture.release()
        cv2.destroyAllWindows()
    return fit(estimator, vault, (width, height), camera, model, train, held)


def fit(
    estimator: object, vault: Path, monitor: tuple[int, int], camera: int, model: str, train: tuple, held: tuple
) -> dict:
    """Train on the nine targets, measure on the four held out, then train on all and save."""
    import numpy as np

    (features, points), (held_features, held_points) = train, held
    if len(features) < 30 or len(held_features) < 8:
        raise RuntimeError("Too few frames with a face. Sit facing the camera in even light and try again.")
    estimator.train(np.array(features), np.array(points))
    errors = np.linalg.norm(estimator.predict(np.array(held_features)) - np.array(held_points), axis=1)
    # The reported error is the held-out one; the saved model also learns from those targets.
    estimator.train(np.array(list(features) + list(held_features)), np.array(list(points) + list(held_points)))
    folder = vault / "calibration"
    folder.mkdir(parents=True, exist_ok=True)
    estimator.save_model(folder / MODEL_FILE)
    error = float(np.median(errors))
    result = {
        "t": datetime.now(UTC).isoformat(),
        "monitor": list(monitor),
        "camera": camera,
        "model": model,
        "model_file": MODEL_FILE,
        "error_px": round(error, 1),
        "error_p90_px": round(float(np.percentile(errors, 90)), 1),
        "usable": error <= usable_limit(monitor[0]),
        "frames": {"train": len(features), "held_out": len(held_features)},
    }
    (folder / CALIBRATION_FILE).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def load(vault: Path) -> dict | None:
    path = vault / "calibration" / CALIBRATION_FILE
    if not path.is_file() or not (vault / "calibration" / MODEL_FILE).is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))

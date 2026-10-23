"""Calibration fitting with EyeTrax's real models, on synthetic eye features."""

import json
from pathlib import Path

import numpy as np
from eyetrax.models import create_model
from gaze import calibrate
from gaze.calibrate import HELD_OUT, TRAIN_ORDER


class Estimator:
    """EyeTrax's GazeEstimator without its camera and face landmarker: the same model calls."""

    def __init__(self, name: str = "ridge") -> None:
        self.model = create_model(name)

    def train(self, X: np.ndarray, y: np.ndarray) -> None:
        self.model.train(X, y)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(X)

    def save_model(self, path: Path) -> None:
        self.model.save(path)


def frames(
    targets: list[tuple[int, int]], noise: float, rng: np.random.Generator, per_target: int = 20
) -> tuple[list, list]:
    """Features that depend linearly on the target, plus noise, like iris offsets do over a small range."""
    mix = np.random.default_rng(7).normal(size=(2, 12))
    features, points = [], []
    for x, y in targets:
        for _ in range(per_target):
            features.append(np.array([x / 1920, y / 1080]) @ mix + rng.normal(scale=noise, size=12))
            points.append([x, y])
    return features, points


def grid(width: int, height: int) -> tuple[list, list]:
    from eyetrax.calibration.common import compute_grid_points

    return compute_grid_points(TRAIN_ORDER, width, height), [(int(width * x), int(height * y)) for x, y in HELD_OUT]


def test_a_clean_calibration_is_usable_and_saved(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    train_targets, held_targets = grid(1920, 1080)
    result = calibrate.fit(
        Estimator(),
        tmp_path,
        (1920, 1080),
        0,
        "ridge",
        frames(train_targets, 0.01, rng),
        frames(held_targets, 0.01, rng),
    )
    assert result["usable"] and result["error_px"] < 60
    assert json.loads((tmp_path / "calibration/gaze.json").read_text())["error_px"] == result["error_px"]
    assert calibrate.load(tmp_path)["monitor"] == [1920, 1080]
    from eyetrax.models import BaseModel

    model = BaseModel.load(tmp_path / "calibration" / calibrate.MODEL_FILE)
    center = frames([(960, 540)], 0.0, rng, per_target=1)[0]
    assert np.allclose(model.predict(np.array(center))[0], [960, 540], atol=40)


def test_a_noisy_calibration_is_marked_unusable(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    train_targets, held_targets = grid(1920, 1080)
    result = calibrate.fit(
        Estimator(), tmp_path, (1920, 1080), 0, "ridge", frames(train_targets, 0.5, rng), frames(held_targets, 0.5, rng)
    )
    assert not result["usable"] and result["error_px"] > 150

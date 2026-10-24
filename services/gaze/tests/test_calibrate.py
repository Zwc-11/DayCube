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


def test_the_head_model_calibrates_from_faces(tmp_path: Path) -> None:
    """The default calibration: synthetic faces (tests/test_head.py) through the head-aware model,
    the same nine-plus-four protocol, saved where the tracker loads it."""
    from gaze.head import HeadEstimator
    from test_head import HOME, MOVED, SCREEN, Faces, face

    width, height = 1920, 1080
    faces = Faces()
    estimator = HeadEstimator((width, height), SCREEN.mm_per_px, landmarker=faces)
    rng = np.random.default_rng(2)

    def capture(targets: list[tuple[int, int]]) -> tuple[list, list]:
        features, points = [], []
        for target in targets:
            for _ in range(10):
                faces.queue.append(face(target, *HOME, rng))
                features.append(estimator.extract_features(None)[0])
                points.append(list(target))
        return features, points

    train_targets, held_targets = grid(width, height)
    result = calibrate.fit(
        estimator,
        tmp_path,
        (width, height),
        0,
        "head",
        capture(train_targets),
        capture(held_targets),
        pitch=(0.277, "monitor"),
    )
    assert result["model"] == "head" and result["model_file"] == calibrate.HEAD_MODEL_FILE
    assert result["usable"] and result["error_px"] < 40
    assert result["mm_per_px"] == 0.277 and result["mm_per_px_source"] == "monitor"
    loaded = calibrate.load(tmp_path)
    assert loaded == result

    tracker_side = calibrate.estimator_for("head", (width, height), loaded["mm_per_px"], landmarker=faces)
    tracker_side.load_model(tmp_path / "calibration" / loaded["model_file"])
    faces.queue.append(face((1500, 300), *MOVED[0], rng))
    features, _ = tracker_side.extract_features(None)
    assert np.allclose(tracker_side.predict(features[None, :])[0], [1500, 300], atol=50)


def test_a_calibration_without_its_model_file_is_not_loaded(tmp_path: Path) -> None:
    folder = tmp_path / "calibration"
    folder.mkdir()
    (folder / calibrate.CALIBRATION_FILE).write_text(json.dumps({"model": "head", "model_file": "gaze-head.json"}))
    assert calibrate.load(tmp_path) is None
    (folder / "gaze-head.json").write_text("{}")
    assert calibrate.load(tmp_path)["model"] == "head"


def test_a_mirrored_webcam_image_is_caught(tmp_path: Path) -> None:
    """A mirrored image (some drivers, virtual cameras) swaps the person's left and right: the eyes
    seem to turn the wrong way. The calibration notices and asks for the frames flipped back."""
    import pytest
    from gaze.face import Face, canonical
    from gaze.head import HeadEstimator
    from test_head import HOME, SIZE, Faces, face

    mesh = canonical()[0]
    mirror = np.array([np.argmin(np.linalg.norm(mesh - mesh[i] * [-1, 1, 1], axis=1)) for i in range(468)])
    mirror = np.r_[mirror, 473, 474, 475, 476, 477, 468, 469, 470, 471, 472]

    def mirrored(f: Face) -> Face:
        points = f.points[mirror].copy()
        points[:, 0] = SIZE[0] - points[:, 0]
        return Face(points, f.size)

    faces = Faces()
    estimator = HeadEstimator((1920, 1080), 0.277, landmarker=faces)
    rng = np.random.default_rng(4)

    def capture(targets: list[tuple[int, int]], flip: bool) -> tuple[list, list]:
        features, points = [], []
        for target in targets:
            for _ in range(5):
                f = face(target, *HOME, rng)
                faces.queue.append(mirrored(f) if flip else f)
                features.append(estimator.extract_features(None)[0])
                points.append(list(target))
        return features, points

    train_targets, held_targets = grid(1920, 1080)
    with pytest.raises(calibrate.Mirrored):
        calibrate.fit(
            estimator, tmp_path, (1920, 1080), 0, "head", capture(train_targets, True), capture(held_targets, True)
        )
    estimator.mirror = True  # the real calibration flips the frames back and captures again
    result = calibrate.fit(
        estimator, tmp_path, (1920, 1080), 0, "head", capture(train_targets, False), capture(held_targets, False)
    )
    assert result["mirrored_camera"] is True
    loaded = HeadEstimator((1920, 1080), 0.277, landmarker=faces)
    loaded.load_model(tmp_path / "calibration" / calibrate.HEAD_MODEL_FILE)
    assert loaded.mirror is True

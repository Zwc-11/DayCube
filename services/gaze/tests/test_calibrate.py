"""Choosing and saving a gaze model from calibration frames: synthetic faces (tests/test_head.py)
posed in front of a camera, the head still for the training and held-out dots and moved for the
last four, run through every candidate model."""

import json
from pathlib import Path

import numpy as np
import pytest
from gaze import calibrate
from gaze.calibrate import MOVED, STILL, TRAIN, Frame
from gaze.face import Face, mirrored
from test_head import HOME, SIZE, face
from test_head import MOVED as POSES

MONITOR = (1920, 1080)
PITCH = (531.0 / 1920, "monitor")


def session(moved_pose: tuple, rng: np.random.Generator, flip: bool = False, per_dot: int = 10) -> list[Frame]:
    frames, t = [], 1_800_000_000.0
    plan = [(TRAIN, p, HOME) for p in calibrate.grid_points(*MONITOR)]
    plan += [(STILL, p, HOME) for p in calibrate.held_out_points(*MONITOR)]
    plan += [(MOVED, p, moved_pose) for p in calibrate.held_out_points(*MONITOR)]
    for phase, target, (R, translation) in plan:
        t += 1.0
        for _ in range(per_dot):
            t += 1 / 30
            points = face(target, R, translation, rng).points
            if flip:
                points = mirrored(points, SIZE[0])
            frames.append(Frame(t, phase, target, SIZE, points, points.copy()))
    return frames


def test_the_head_aware_model_wins_once_the_head_moves(tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    result = calibrate.choose(session(POSES[3], rng), tmp_path, MONITOR, 0, PITCH)
    scores = result["candidates"]
    assert set(scores) == {"head", "head-video", "ridge"}
    # EyeTrax is fine with the head where it was and lost once it moves; the head-aware model is not.
    assert scores["ridge"]["moved_px"] > 3 * scores["head"]["moved_px"]
    assert scores["head"]["moved_px"] < 60 and scores["head"]["still_px"] < 40
    assert result["model"] == "head" and result["model_file"] == calibrate.HEAD_MODEL_FILE
    assert result["usable"] and result["error_px"] == scores["head"]["still_px"]
    assert result["moved_error_px"] == scores["head"]["moved_px"]
    assert result["head_moved_mm"] == pytest.approx(99, abs=10)  # MOVED[3] is ~10 cm from HOME
    assert result["frames"] == {"train": 90, "still": 40, "moved": 40}
    assert result["mm_per_px_source"] == "monitor" and "mirrored_camera" not in result
    assert calibrate.load(tmp_path) == result

    # The saved model runs in the tracker.
    from test_head import Faces

    faces = Faces()
    tracker_side = calibrate.estimator_for("head", MONITOR, result["mm_per_px"], landmarker=faces)
    tracker_side.load_model(tmp_path / "calibration" / result["model_file"])
    faces.queue.append(face((1500, 300), *POSES[0], rng))
    features, _ = tracker_side.extract_features(None)
    assert np.allclose(tracker_side.predict(features[None, :])[0], [1500, 300], atol=50)

    # Every frame is kept, numbers only, to refit later.
    frames = calibrate.load_frames(tmp_path / "calibration" / calibrate.FRAMES_FILE)
    assert len(frames) == 170 and frames[0].image.shape == (478, 3) and frames[-1].phase == MOVED


def test_eyetrax_can_win_with_the_head_still(tmp_path: Path) -> None:
    """Without head movement the choice is open; a forced model is fitted alone."""
    rng = np.random.default_rng(3)
    result = calibrate.choose(session(HOME, rng), tmp_path, MONITOR, 0, PITCH, model="ridge")
    assert set(result["candidates"]) == {"ridge"} and result["model"] == "ridge"
    assert result["model_file"] == calibrate.MODEL_FILE and result["head_moved_mm"] is None
    from eyetrax.models import BaseModel

    assert BaseModel.load(tmp_path / "calibration" / calibrate.MODEL_FILE) is not None


def test_a_mirrored_webcam_image_is_flipped_back(tmp_path: Path) -> None:
    rng = np.random.default_rng(4)
    result = calibrate.choose(session(POSES[0], rng, flip=True), tmp_path, MONITOR, 0, PITCH)
    assert result["mirrored_camera"] is True and result["model"].startswith("head")
    assert result["candidates"]["head"]["moved_px"] < 60
    from gaze.head import HeadEstimator

    loaded = HeadEstimator(MONITOR, PITCH[0], landmarker=object())
    loaded.load_model(tmp_path / "calibration" / calibrate.HEAD_MODEL_FILE)
    assert loaded.mirror is True


def test_too_few_faces_is_an_error(tmp_path: Path) -> None:
    frames = session(HOME, np.random.default_rng(5), per_dot=2)
    with pytest.raises(RuntimeError, match="Too few frames"):
        calibrate.choose(frames, tmp_path, MONITOR, 0, PITCH)


def test_errors_are_scored_after_the_trackers_smoother() -> None:
    rng = np.random.default_rng(6)
    frames = [Frame(i / 30, STILL, (500, 500), SIZE, None, None) for i in range(30)]
    jitter = np.array([[500.0, 500.0]] * 30) + rng.normal(0, 60, (30, 2))
    raw, smoothed = calibrate.errors(jitter, frames)
    assert np.median(smoothed[5:]) < 0.5 * np.median(raw[5:])
    # A new dot restarts the smoother instead of dragging the last one's position along.
    frames += [Frame(1 + i / 30, STILL, (1500, 500), SIZE, None, None) for i in range(30)]
    raw, smoothed = calibrate.errors(np.r_[np.full((30, 2), 500.0), np.tile([1500.0, 500.0], (30, 1))], frames)
    assert smoothed[30] == 0.0


def test_the_mirror_map_is_an_involution() -> None:
    from gaze.face import mirror_index

    m = mirror_index()
    assert (m[m] == np.arange(478)).all() and m[33] == 263 and m[468] == 473
    f = face((900, 500), *HOME)
    assert np.allclose(mirrored(mirrored(f.points, SIZE[0]), SIZE[0]), f.points)
    assert isinstance(Face(f.points, SIZE), Face)


def test_a_calibration_without_its_model_file_is_not_loaded(tmp_path: Path) -> None:
    folder = tmp_path / "calibration"
    folder.mkdir()
    (folder / calibrate.CALIBRATION_FILE).write_text(json.dumps({"model": "head", "model_file": "gaze-head.json"}))
    assert calibrate.load(tmp_path) is None
    (folder / "gaze-head.json").write_text("{}")
    assert calibrate.load(tmp_path)["model"] == "head"


def test_the_report_marks_the_model_in_use() -> None:
    from gaze.__main__ import report

    result = {
        "monitor": [2560, 1440],
        "model": "head",
        "error_px": 58.0,
        "head_moved_mm": 12.0,
        "candidates": {
            "head": {"still_px": 58.0, "still_raw_px": 139.0, "moved_px": 64.0, "moved_raw_px": 150.0},
            "ridge": {"still_px": 45.0, "still_raw_px": 69.0, "moved_px": None, "moved_raw_px": None},
        },
    }
    text = report(result)
    assert "head-aware, image landmarks" in text and "58 (139)" in text and "<- using" in text
    assert "EyeTrax ridge (old)" in text and " -" in text
    assert "too little to test head movement" in text and "usable up to 160 px" in text

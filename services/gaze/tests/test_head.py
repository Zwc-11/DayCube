"""gaze.head on synthetic faces: a canonical face posed in front of a camera, its eyeballs turned
to look at a known screen point, projected into the image. Calibrated with the head still, the
model must keep finding the target when the head moves and turns; a plain regression from the
same eye measurements to screen pixels must not (that is EyeTrax's failure)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")

from gaze.face import LEFT_EYE, LEFT_IRIS, RIGHT_EYE, RIGHT_IRIS, Face, canonical  # noqa: E402
from gaze.head import (  # noqa: E402
    EYEBALL_DEPTH_MM,
    EYEBALL_MM,
    Camera,
    GazeModel,
    HeadEstimator,
    Poser,
    Screen,
    _head_frame,
    directions,
    pack,
    unpack,
)

SIZE = (1280, 720)
CAMERA = Camera.nominal(*SIZE)
SCREEN = Screen.below_camera((1920, 1080), 531.0)  # 24"
IRIS_MM = 5.9


def _rotation(yaw: float, pitch: float, roll: float = 0.0) -> np.ndarray:
    y, p, r = (math.radians(a) for a in (yaw, pitch, roll))
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    Rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


def face(target_px: tuple[float, float], R: np.ndarray, t: np.ndarray, rng: np.random.Generator | None = None) -> Face:
    """The canonical face at pose (R, t), both eyes turned to the screen pixel."""
    model = _head_frame(canonical()[0])
    target = SCREEN.point(np.array(target_px))
    points = np.zeros((478, 3))
    points[:468] = model
    for iris, eye in ((RIGHT_IRIS, RIGHT_EYE), (LEFT_IRIS, LEFT_EYE)):
        centre = model[list(eye[:2])].mean(0) + np.array([0, 0, EYEBALL_DEPTH_MM])
        gaze = R.T @ (target - (R @ centre + t))
        gaze /= np.linalg.norm(gaze)
        front = centre + EYEBALL_MM * gaze
        side = np.cross(gaze, [0, 1, 0])
        side /= np.linalg.norm(side)
        up = np.cross(side, gaze)
        points[iris] = front
        points[iris + 1 : iris + 5] = front + IRIS_MM * np.array([side, up, -side, -up])
        # The lids follow the eye up and down a little.
        points[[eye[2], eye[3]]] += np.array([0, 4.0 * gaze[1], 0])
    camera_points = points @ R.T + t
    pixels = np.c_[
        CAMERA.f * camera_points[:, 0] / camera_points[:, 2] + CAMERA.cx,
        CAMERA.f * camera_points[:, 1] / camera_points[:, 2] + CAMERA.cy,
        np.zeros(478),
    ]
    if rng is not None:
        pixels[:, :2] += rng.normal(0, 0.15, (478, 2))
    return Face(pixels, SIZE)


GRID = [(x, y) for y in (100, 540, 980) for x in (150, 960, 1770)] + [(560, 320), (1360, 320), (560, 760), (1360, 760)]
HOME = (np.eye(3), np.array([0.0, 180.0, 600.0]))  # 60 cm out, eyes level with the screen's upper part
MOVED = [
    (_rotation(0, 0), np.array([80.0, 180.0, 600.0])),  # 8 cm to the side
    (_rotation(0, 0), np.array([0.0, 230.0, 700.0])),  # back 10 cm and lower
    (_rotation(12, 0), np.array([0.0, 180.0, 600.0])),  # turned 12 degrees
    (_rotation(-8, 10, 4), np.array([-50.0, 150.0, 520.0])),  # everything at once
]
TESTS = [(300, 250), (1500, 300), (900, 800), (1700, 1000), (200, 900)]


def test_geometry_round_trip() -> None:
    eyes = np.array([[30.0, 150.0, 550.0]])
    for pixel in ((0.0, 0.0), (1919.0, 1079.0), (700.0, 300.0)):
        aim = SCREEN.point(np.array(pixel)) - eyes
        assert SCREEN.pixel(eyes, aim / np.linalg.norm(aim))[0] == pytest.approx(pixel, abs=1e-6)
    xy = np.array([[0.2, -0.1], [0.0, 0.0]])
    assert directions(xy)[:, :2] == pytest.approx(xy)
    assert np.linalg.norm(directions(xy), axis=1) == pytest.approx(1.0)
    # A ray away from the screen never meets it.
    assert np.isnan(SCREEN.pixel(eyes, np.array([[0.0, 0.0, 1.0]]))).all()


def test_canonical_face_is_millimetre_scaled() -> None:
    model = _head_frame(canonical()[0])
    width = np.linalg.norm(model[33] - model[263])
    assert 80 < width < 100  # outer eye corners about 9 cm apart
    assert model[1, 2] < model[33, 2]  # the nose tip is nearer the camera than the eye corners


def test_pose_is_recovered() -> None:
    poser = Poser(CAMERA)
    R, t = _rotation(10, -5, 3), np.array([40.0, 120.0, 650.0])
    R_hat, t_hat = poser.pose(face((960, 540), R, t).points)
    assert np.degrees(np.arccos((np.trace(R.T @ R_hat) - 1) / 2)) < 0.5
    assert np.linalg.norm(t_hat - t) < 3.0


def test_head_movement_is_compensated() -> None:
    rng = np.random.default_rng(3)
    poser = Poser(CAMERA)
    calibration = [poser.measure(face(p, *HOME, rng)) for p in GRID for _ in range(3)]
    targets = np.array([p for p in GRID for _ in range(3)], dtype=float)
    model = GazeModel(SCREEN).fit(calibration, targets)
    # Plain regression from the same measurements straight to pixels, as EyeTrax does.
    X = np.array([h.features.ravel() for h in calibration])
    A = np.c_[np.ones(len(X)), X]
    plain = np.linalg.lstsq(A, targets, rcond=None)[0]

    home = [poser.measure(face(p, *HOME, rng)) for p in TESTS]
    # About 20 px is the 0.15 px landmark noise; without noise the model is exact.
    assert np.median(np.linalg.norm(model.predict(home) - TESTS, axis=1)) < 30
    for pose in MOVED:
        heads = [poser.measure(face(p, *pose, rng)) for p in TESTS]
        ours = np.linalg.norm(model.predict(heads) - TESTS, axis=1)
        theirs = np.linalg.norm(np.c_[np.ones(len(heads)), [h.features.ravel() for h in heads]] @ plain - TESTS, axis=1)
        assert np.median(ours) < 30, pose
        assert np.median(theirs) > 150, pose


def test_noise_free_faces_are_exact() -> None:
    poser = Poser(CAMERA)
    model = GazeModel(SCREEN).fit([poser.measure(face(p, *HOME)) for p in GRID], np.array(GRID, dtype=float))
    for pose in MOVED:
        heads = [poser.measure(face(p, *pose)) for p in TESTS]
        assert np.abs(model.predict(heads) - TESTS).max() < 1.0, pose


class Faces:
    """A FaceLandmarker stand-in: hands out prepared faces, one per frame."""

    def __init__(self) -> None:
        self.queue: list[Face | None] = []
        self.closed = False

    def detect(self, frame: object) -> Face | None:
        return self.queue.pop(0)

    def close(self) -> None:
        self.closed = True


def test_estimator_calibrates_saves_and_tracks(tmp_path: Path) -> None:
    """The EyeTrax-shaped interface the tracker and the calibration screens use."""
    faces = Faces()
    estimator = HeadEstimator((1920, 1080), 531.0 / 1920, landmarker=faces)
    rng = np.random.default_rng(5)
    X, y = [], []
    for p in GRID:
        for _ in range(3):
            faces.queue.append(face(p, *HOME, rng))
            features, blink = estimator.extract_features(np.zeros((720, 1280, 3)))
            assert features.shape == (26,) and not blink
            X.append(features)
            y.append(p)
    estimator.train(np.array(X), np.array(y))
    estimator.save_model(tmp_path / "gaze-head.json")

    loaded = HeadEstimator((1920, 1080), 531.0 / 1920, landmarker=faces)
    loaded.load_model(tmp_path / "gaze-head.json")
    R, t = MOVED[3]
    faces.queue.append(face((900, 800), R, t, rng))
    features, _ = loaded.extract_features(np.zeros((720, 1280, 3)))
    assert loaded.predict(features[None, :])[0] == pytest.approx([900, 800], abs=40)
    moved_mm, turned_deg = loaded.moved()
    assert moved_mm == pytest.approx(np.linalg.norm(t - HOME[1]), abs=5)
    assert turned_deg == pytest.approx(14.0, abs=1.5)

    faces.queue.append(None)
    assert loaded.extract_features(np.zeros((720, 1280, 3))) == (None, False)
    loaded.close()
    assert faces.closed


def test_pack_round_trip() -> None:
    poser = Poser(CAMERA)
    head = poser.measure(face((400, 300), *MOVED[2]))
    (back,) = unpack(pack(head))
    for name in ("R", "t", "eyes", "features"):
        assert np.array_equal(getattr(back, name), getattr(head, name))


def test_blinks_are_skipped() -> None:
    faces = Faces()
    estimator = HeadEstimator((1920, 1080), 0.277, landmarker=faces)
    for _ in range(20):
        faces.queue.append(face((960, 540), *HOME))
        assert not estimator.extract_features(None)[1]
    shut = face((960, 540), *HOME)
    for _outer, _inner, upper, lower in ((33, 133, 159, 145), (263, 362, 386, 374)):
        middle = (shut.points[upper] + shut.points[lower]) / 2
        shut.points[[upper, lower]] = middle
    faces.queue.append(shut)
    assert estimator.extract_features(None)[1]

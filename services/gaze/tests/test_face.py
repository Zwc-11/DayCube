"""Frame preparation with synthetic pixels and native MediaPipe Image ownership; no camera."""

from types import SimpleNamespace

import cv2
import mediapipe as mp
import numpy as np
import pytest
from gaze import face as face_module
from gaze.face import Landmarker
from gaze.head import HeadEstimator


class Detector:
    def __init__(self) -> None:
        self.images: list[mp.Image] = []
        self.timestamps: list[int] = []
        self.result = SimpleNamespace(face_landmarks=[[SimpleNamespace(x=0.25, y=0.5, z=-0.1)] * 478])
        self.closed = False

    def detect(self, image: mp.Image) -> object:
        self.images.append(image)
        return self.result

    def detect_for_video(self, image: mp.Image, timestamp: int) -> object:
        self.timestamps.append(timestamp)
        return self.detect(image)

    def close(self) -> None:
        self.closed = True


def landmarker(video: bool = False) -> tuple[Landmarker, Detector]:
    # Substitute only the neural detector, so the real Image wrapper's buffer ownership is tested.
    result = object.__new__(Landmarker)
    native = Detector()
    result._mp, result._landmarker, result.video, result._last_ms, result._rgb = mp, native, video, 0, None
    return result, native


@pytest.mark.parametrize("video", [False, True])
def test_rgb_scratch_is_reused_without_changing_prior_images(video: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_module.time, "monotonic", lambda: 1.0)
    tracker, native = landmarker(video)
    pixels = np.arange(8 * 12 * 3, dtype=np.uint8).reshape(8, 12, 3)
    first = tracker.detect(pixels)
    scratch = tracker._rgb
    expected = cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)
    original_points = first.points.copy()
    assert scratch is not None and np.array_equal(native.images[0].numpy_view(), expected)
    assert not np.shares_memory(native.images[0].numpy_view(), scratch)
    second = tracker.detect(255 - pixels)
    assert tracker._rgb is scratch
    assert np.array_equal(native.images[0].numpy_view(), expected)
    assert np.array_equal(native.images[1].numpy_view(), 255 - expected)
    assert np.array_equal(first.points, original_points) and not np.shares_memory(first.points, second.points)
    assert native.timestamps == ([1000, 1001] if video else [])
    tracker.close()
    assert tracker._rgb is None and native.closed


def test_rgb_scratch_follows_resolution_and_selective_landmarks() -> None:
    tracker, native = landmarker()
    tracker.detect(np.zeros((8, 12, 3), dtype=np.uint8))
    old = tracker._rgb
    result = tracker.detect(np.full((4, 9, 3), 17, dtype=np.uint8), only=np.array([33, 468]))
    assert tracker._rgb is not old and tracker._rgb.shape == (4, 9, 3)
    assert result.size == (9, 4)
    assert result.points[[33, 468]] == pytest.approx(np.array([[2.25, 2.0, -0.9]] * 2))
    assert np.isnan(np.delete(result.points, [33, 468], axis=0)).all()
    # cvtColor also accepts BGRA; its RGB output still has three channels.
    scratch = tracker._rgb
    bgra = np.full((4, 9, 4), [1, 2, 3, 255], dtype=np.uint8)
    tracker.detect(bgra)
    assert tracker._rgb is scratch
    assert np.array_equal(native.images[-1].numpy_view(), cv2.cvtColor(bgra, cv2.COLOR_BGR2RGB))
    native.result = SimpleNamespace(face_landmarks=[])
    assert tracker.detect(bgra) is None
    tracker.close()


def test_pixel_scratch_can_be_dropped_without_reloading_the_detector() -> None:
    tracker, native = landmarker()
    estimator = HeadEstimator((1920, 1080), 0.277, landmarker=tracker)
    tracker.detect(np.zeros((8, 12, 3), dtype=np.uint8))
    previous = tracker._rgb
    estimator.release_frame_buffers()
    assert tracker._rgb is None and not native.closed
    assert tracker.detect(np.ones((8, 12, 3), dtype=np.uint8)) is not None
    assert tracker._rgb is not previous
    estimator.close()

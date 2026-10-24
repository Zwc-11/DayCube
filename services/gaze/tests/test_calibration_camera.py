"""Calibration camera ownership and abort cleanup, without a camera or visible window."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest
from gaze import calibrate, system, track
from gaze import camera as camera_module


def test_calibration_uses_fresh_frames_and_releases_on_cancel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    events: list[object] = []
    camera = SimpleNamespace(release=lambda: events.append("release"))
    monkeypatch.setattr(system, "primary_monitor", lambda: (1920, 1080))
    monkeypatch.setattr(system, "monitor_mm_per_px", lambda: (0.277, "test"))
    monkeypatch.setattr(camera_module, "open_camera", lambda index, mode: events.append(index) or camera)
    monkeypatch.setattr(camera_module, "probe", lambda *args, **kwargs: pytest.fail("no webcam probing in tests"))

    def latest(opener: Callable[[], object]) -> object:
        events.append("latest")
        return opener()

    monkeypatch.setattr(track, "open_camera_thread", latest)
    monkeypatch.setattr(cv2, "namedWindow", lambda *args: None)
    monkeypatch.setattr(cv2, "setWindowProperty", lambda *args: None)
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: events.append("windows closed"))

    class Session:
        def __init__(self, opened: object, width: int, height: int) -> None:
            assert opened is camera and (width, height) == (1920, 1080)

        def wait_for_face(self) -> None:
            raise calibrate.Aborted

        def close(self) -> None:
            events.append("landmarkers closed")

    monkeypatch.setattr(calibrate, "Session", Session)
    with pytest.raises(calibrate.Aborted):
        calibrate.calibrate(tmp_path, camera=2, fps=30)
    assert events == ["latest", 2, "release", "landmarkers closed", "windows closed"]

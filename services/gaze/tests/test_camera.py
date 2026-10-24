"""Finding a webcam mode that really gives 60 frames a second, and opening it again to track."""

import numpy as np
import pytest
from gaze import camera
from gaze.track import LatestFrame


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeCapture:
    """Delivers frames of ``size`` at ``fps`` on a fake clock: each read takes one frame interval."""

    def __init__(self, fps: float, size: tuple[int, int], clock: Clock) -> None:
        self.fps, self.size, self.clock = fps, size, clock
        self.released = False

    def read(self) -> tuple[bool, np.ndarray]:
        self.clock.t += 1 / self.fps
        return True, np.zeros((self.size[1], self.size[0], 3), dtype=np.uint8)

    def release(self) -> None:
        self.released = True


def test_measure_times_frames_rather_than_trusting_the_driver() -> None:
    clock = Clock()
    fps, size = camera.measure(FakeCapture(59.9, (1280, 720), clock), seconds=2.0, clock=clock)
    assert fps == pytest.approx(59.9, rel=0.02) and size == (1280, 720)


def test_probe_keeps_the_first_mode_that_delivers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(camera, "BACKENDS", ("msmf", "dshow"))
    clock = Clock()
    # Media Foundation opens MJPG 720p but delivers 30; DirectShow fails 720p, gives 640x480 at 60.
    delivered = {("msmf", 1280): 30.0, ("msmf", 640): 30.0, ("dshow", 640): 60.0}
    opened: list[FakeCapture] = []
    lines: list[str] = []

    def opener(index: int, mode: dict) -> FakeCapture:
        rate = delivered.get((mode["backend"], mode["width"]))
        if rate is None:
            raise RuntimeError("no such mode")
        opened.append(FakeCapture(rate, (mode["width"], mode["height"]), clock))
        return opened[-1]

    mode = camera.probe(0, 60, opener=opener, measurer=lambda c: camera.measure(c, clock=clock), report=lines.append)
    assert mode == {"width": 640, "height": 480, "fps": 60, "fourcc": "MJPG", "backend": "dshow", "measured_fps": 60.0}
    assert all(c.released for c in opened) and len(lines) == 4 and "does not open" in lines[2]
    assert camera.describe(mode) == "640x480 MJPG 60 fps" and camera.describe(None) == "the webcam's default mode"
    # Nothing that fast: the default mode.
    assert camera.probe(0, 60, opener=lambda i, m: FakeCapture(30, (640, 480), clock),
                        measurer=lambda c: camera.measure(c, clock=clock)) is None  # fmt: skip


def test_the_tracker_reports_the_rate_frames_really_arrive() -> None:
    import threading
    import time

    class Webcam:
        def __init__(self) -> None:
            self.stop = threading.Event()

        def read(self) -> tuple[bool, object]:
            time.sleep(1 / 60)
            return True, object()

        def release(self) -> None:
            pass

    latest = LatestFrame(Webcam)
    try:
        assert latest.first(2.0)
        time.sleep(0.6)
        rate = latest.rate()
        assert rate is not None and 30 < rate < 75  # about 60 (sleep granularity on a busy machine)
    finally:
        latest.release()

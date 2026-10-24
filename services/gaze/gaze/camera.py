"""Opening the webcam in a mode that gives 60 frames a second, where it can.

Most webcams deliver 30 frames a second in their default mode (often 640 x 480, uncompressed) and
60 only when asked for MJPG at a size they support it in (1280 x 720 or 640 x 480 on most 60 fps
webcams). OpenCV asks for none of that by default. A gaze point is measured once per frame, so
the frame rate is the gaze rate: 60 fps halves the time between points (33 to 17 ms) and the
ring's delay with it.

``probe`` tries each mode (with Media Foundation, then DirectShow, which honours MJPG on more
drivers) and keeps the first that really delivers about the asked rate, measured by timing frames,
not by what the driver says. Calibration probes and saves the mode; tracking opens the same mode,
because the gaze model is fitted to one image size and field of view.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any

MODES = (
    {"width": 1280, "height": 720, "fps": 60, "fourcc": "MJPG"},
    {"width": 640, "height": 480, "fps": 60, "fourcc": "MJPG"},
)
BACKENDS = ("msmf", "dshow") if sys.platform == "win32" else ("any",)
ENOUGH = 0.85  # a mode must deliver this share of its frame rate to count


def describe(mode: dict | None) -> str:
    if not mode:
        return "the webcam's default mode"
    return f"{mode['width']}x{mode['height']} {mode.get('fourcc') or 'default format'} {mode['fps']:g} fps"


def open_camera(index: int, mode: dict | None = None) -> Any:
    """The webcam ``index`` in ``mode`` (from ``probe`` or a saved calibration), or in its default
    mode when there is none, opened exactly as before."""
    if not mode:
        from eyetrax.utils.video import open_camera as default

        return default(index)
    import cv2

    api = {"msmf": cv2.CAP_MSMF, "dshow": cv2.CAP_DSHOW}.get(mode.get("backend", "any"), cv2.CAP_ANY)
    capture = cv2.VideoCapture(index, api)
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"cannot open camera {index} ({mode.get('backend', 'any')})")
    if mode.get("fourcc"):
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*mode["fourcc"]))
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, mode["width"])
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, mode["height"])
    capture.set(cv2.CAP_PROP_FPS, mode["fps"])
    return capture


def measure(
    capture: Any, seconds: float = 2.0, warmup: float = 0.6, clock: Callable[[], float] = time.monotonic
) -> tuple[float, tuple[int, int] | None]:
    """Frames per second the camera really delivers (after a warm-up: the first frames of a newly
    opened webcam come slowly), and the frame size (width, height); size None when no frame came."""
    start = clock()
    size = None
    while clock() - start < warmup:
        ok, frame = capture.read()
        if ok and frame is not None:
            size = (frame.shape[1], frame.shape[0])
    frames = 0
    start = clock()
    while clock() - start < seconds:
        ok, frame = capture.read()
        if ok and frame is not None:
            frames += 1
            size = (frame.shape[1], frame.shape[0])
    elapsed = clock() - start
    return (frames / elapsed if elapsed > 0 else 0.0), size


def probe(
    index: int,
    wanted_fps: float = 60.0,
    opener: Callable[[int, dict], Any] = open_camera,
    measurer: Callable[[Any], tuple[float, tuple[int, int] | None]] = measure,
    report: Callable[[str], None] = lambda _: None,
) -> dict | None:
    """The first mode that delivers at least ``ENOUGH`` of ``wanted_fps``, as the mode to open
    (with the size the camera really gave and the rate measured), or None: keep the default mode."""
    for backend in BACKENDS:
        for mode in MODES:
            if mode["fps"] < wanted_fps:
                continue
            candidate = {**mode, "backend": backend}
            try:
                capture = opener(index, candidate)
            except Exception as error:  # noqa: BLE001 - a mode or backend this camera lacks
                report(f"  {describe(candidate)} via {backend}: does not open ({error})")
                continue
            try:
                fps, size = measurer(capture)
            finally:
                capture.release()
            report(
                f"  {describe(candidate)} via {backend}: {fps:.0f} frames/s"
                + (f", {size[0]}x{size[1]}" if size else "")
            )
            if size is not None and fps >= ENOUGH * mode["fps"]:
                return {**candidate, "width": size[0], "height": size[1], "measured_fps": round(fps, 1)}
    return None

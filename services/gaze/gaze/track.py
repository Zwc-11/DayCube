"""The tracking loop: webcam frames -> EyeTrax gaze -> One Euro -> I-DT fixations -> receiver.

Frames are processed in memory and dropped; only fixations leave this process. The webcam is
released, and nothing is recorded, while:

- the receiver is paused (from the extension's toolbar button or shortcut), or has not confirmed
  in the last few seconds that it is running and not paused (so a start while paused, or a
  receiver that stops answering, never turns the camera on);
- the session is locked;
- another app is using the webcam (a video call);
- you are away: no keyboard or mouse input for 5 minutes and no face for the last minute.
  Reading without touching anything keeps tracking, since the face is still there.
"""

from __future__ import annotations

import contextlib
import logging
import math
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from .fixations import FixationDetector, Smoother
from .receiver import Receiver
from .system import System

log = logging.getLogger("gaze")

AWAY_IDLE_S = 300
AWAY_NO_FACE_S = 60
BACK_IDLE_S = 5
OFFLINE_EXIT_S = 120  # a supervised tracker outlives a stopped receiver by this much
CONFIRM_S = 5.0  # the camera stays on only this long after the receiver last said "recording"
CHECK_EVERY_S = 2.0
REPORT_EVERY_S = 1.0
DEFAULT_FPS = 30.0


class Camera(Protocol):
    def read(self) -> tuple[bool, Any]: ...
    def release(self) -> None: ...


class Estimator(Protocol):
    def extract_features(self, frame: Any) -> tuple[Any, bool]: ...
    def predict(self, features: Any) -> Any: ...


class Tracker:
    def __init__(
        self,
        estimator: Estimator,
        open_camera: Callable[[], Camera],
        receiver: Receiver,
        calibration: dict,
        *,
        system: System | None = None,
        fps: float = DEFAULT_FPS,
        supervised: bool = False,
        recalibrated: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("fps must be positive and finite")
        self.estimator, self.open_camera, self.receiver = estimator, open_camera, receiver
        self.calibration = calibration
        self.width, self.height = calibration["monitor"]
        self.system = system or System()
        self.period = 1.0 / fps
        self.target_fps = fps
        self.timer = timer
        self._prediction_input: np.ndarray | None = None
        self.supervised = supervised
        self.recalibrated = recalibrated
        self.told_recalibrated = False
        self.clock, self.sleep = clock, sleep
        self.detector = FixationDetector(self.width, self.height)
        self.smoother = Smoother()
        self.camera: Camera | None = None
        self.hold: str | None = None  # why the camera is off on purpose
        self.problem: str | None = None  # why it is off against our wishes
        self.paused = False
        self.confirmed_at = -1e9  # when the receiver last accepted a report (running, not paused)
        self.failures = 0
        self.retry_at = 0.0
        self.checked_at = -1e9
        self.reported_at = -1e9
        self.offline_since: float | None = None
        self.last_face = clock()
        self.frames: deque[tuple[float, bool]] = deque()  # (time, had a face) over the last 10 s
        self.samples: deque[float] = deque()  # gaze samples given to the detector over the last 30 s
        self.fixated: deque[tuple[float, int]] = deque()  # (time, samples) of fixations over the last 30 s

    # -- state -------------------------------------------------------------------------------------

    def state(self) -> dict:
        frames = len(self.frames)
        span = self.frames[-1][0] - self.frames[0][0] if frames > 1 else 0.0
        return {
            "tracking": self.camera is not None and self.hold is None,
            "reason": self.hold or (self.problem if self.camera is None else None),
            "calibrated": True,
            "error_px": self.calibration.get("error_px"),
            "monitor": [self.width, self.height],
            "fps": round((frames - 1) / span, 1) if span > 0 else None,
            "target_fps": self.target_fps,
            "face": round(sum(face for _, face in self.frames) / frames, 2) if frames else None,
            # How much of the gaze becomes fixations: low means the detector is splitting looks.
            "coverage": round(min(1.0, (sum(n for _, n in self.fixated) + self._open()) / len(self.samples)), 2)
            if len(self.samples) >= 30
            else None,
            "noise_px": None if self.detector.noise_px is None else round(self.detector.noise_px, 1),
        }

    def _open(self) -> int:
        """Samples in the fixation still under way, once it is long enough to count."""
        window = self.detector.window
        return len(window) if window and window[-1][0] - window[0][0] >= self.detector.min_duration else 0

    def _fixations(self, now: float, found: list) -> None:
        for fixation in found:
            self.receiver.add(fixation)
            self.fixated.append((now, fixation.samples))
        while self.fixated and now - self.fixated[0][0] > 30:
            self.fixated.popleft()

    def _reason(self, now: float) -> str | None:
        """Why the camera should be off now, or None. Re-evaluated every couple of seconds."""
        if self.recalibrated():
            if self.supervised:
                raise SystemExit("calibration changed")  # the receiver restarts the tracker with it
            if not self.told_recalibrated:
                self.told_recalibrated = True
                log.warning("a new calibration was saved; restart the tracker to use it")
        if self.paused:
            return "paused"
        if now - self.confirmed_at > CONFIRM_S:
            return "receiver not running"
        if self.system.locked():
            return "locked"
        if self.system.camera_busy():
            return "camera busy"
        idle = self.system.idle_seconds()
        if self.hold == "away":
            return "away" if idle > BACK_IDLE_S else None
        if idle > AWAY_IDLE_S and now - self.last_face > AWAY_NO_FACE_S:
            return "away"
        return None

    def _release(self) -> None:
        if self.camera is not None:
            self.camera.release()
            self.camera = None
        for fixation in self.detector.flush():
            self.receiver.add(fixation)
        self.smoother.reset()
        self.frames.clear()
        self.samples.clear()
        self.fixated.clear()
        self._prediction_input = None

    def _report(self, now: float) -> None:
        if now - self.reported_at < REPORT_EVERY_S:
            return
        self.reported_at = now
        result = self.receiver.send(self.state())
        if result in ("ok", "paused"):  # only an answer from the receiver changes the pause state
            if result == "ok":
                self.confirmed_at = now
            if (result == "paused") != self.paused or self.hold == "receiver not running":
                self.checked_at = -1e9  # act on a pause or resume at the next frame, not the next check
            self.paused = result == "paused"
        if result == "offline":
            self.offline_since = self.offline_since or now
            if self.supervised and now - self.offline_since > OFFLINE_EXIT_S:
                raise SystemExit("the receiver stopped")
        else:
            self.offline_since = None
            if result == "refused":
                log.warning("the receiver refused gaze data (vault token?)")

    # -- loop --------------------------------------------------------------------------------------

    def step(self) -> None:
        started = self.timer()
        now = self.clock()
        if now - self.checked_at >= CHECK_EVERY_S:
            self.checked_at = now
            reason = self._reason(now)
            if reason != self.hold:
                log.info("tracking %s", f"held: {reason}" if reason else "resumed")
                if reason is None:
                    self.last_face = now  # "away" restarts its clock when tracking resumes
            self.hold = reason
        if self.hold is not None:
            self._release()
            self._report(now)
            self.sleep(0.5)
            return
        if self.camera is None:
            if now < self.retry_at:
                self._report(now)
                self.sleep(0.5)
                return
            try:
                self.camera = self.open_camera()
                self.failures, self.problem = 0, None
            except Exception as error:  # noqa: BLE001 - any camera failure means try again later
                log.warning("camera unavailable: %s", error)
                self.problem, self.retry_at = "camera unavailable", now + 10
                return
        ok, frame = self.camera.read()
        if not ok:
            self.failures += 1
            if self.failures >= 30:  # unplugged, or lost across sleep: reopen
                log.warning("camera stopped delivering frames; reopening")
                self._release()
                self.problem, self.retry_at = "camera stopped", now + 5
            self._report(now)
            return
        self.failures = 0
        features, blink = self.estimator.extract_features(frame)
        face = features is not None
        self.frames.append((started, face))
        while self.frames and started - self.frames[0][0] > 10:
            self.frames.popleft()
        if face:
            self.last_face = now
        if face and not blink:
            # EyeTrax already returns an ndarray: asarray is a view, and the caller-owned
            # prediction row is reused until shape/dtype changes or tracking releases it.
            values = np.asarray(features)
            if (
                self._prediction_input is None
                or self._prediction_input.shape != (1, values.size)
                or self._prediction_input.dtype != values.dtype
            ):
                self._prediction_input = np.empty((1, values.size), dtype=values.dtype)
            self._prediction_input[0] = values
            x, y = (float(v) for v in self.estimator.predict(self._prediction_input)[0])
            x, y = self.smoother(now, x, y)
            fx, fy = x / self.width, y / self.height
            if -0.05 <= fx <= 1.05 and -0.05 <= fy <= 1.05:  # off-screen samples end the fixation
                self.samples.append(now)
                while now - self.samples[0] > 30:
                    self.samples.popleft()
                self._fixations(now, self.detector.add(now, fx, fy))
        self._report(now)
        self.sleep(max(0.0, self.period - (self.timer() - started)))

    def run(self, should_stop: Callable[[], bool] = lambda: False) -> None:
        try:
            while not should_stop():
                self.step()
        finally:
            self._release()
            self.hold = "stopped"
            self.reported_at = -1e9
            with contextlib.suppress(SystemExit):
                self._report(self.clock())

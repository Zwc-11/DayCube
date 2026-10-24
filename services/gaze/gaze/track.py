"""The tracking loop: webcam frames -> gaze (gaze.head, or an older EyeTrax calibration) -> One
Euro -> I-DT fixations -> receiver.

Frames are processed in memory and dropped; only fixations leave this process, and, while the
receiver says the extension's gaze ring is on screen, the smoothed gaze point ten times a second
(shown, never stored). A completed fixation is reported within a quarter second. The webcam is
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
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from .fixations import FixationDetector, Smoother
from .receiver import Receiver, corrected
from .system import System

log = logging.getLogger("gaze")

AWAY_IDLE_S = 300
AWAY_NO_FACE_S = 60
BACK_IDLE_S = 5
OFFLINE_EXIT_S = 120  # a supervised tracker outlives a stopped receiver by this much
CAMERA_STALL_S = 5.0  # no frame for this long: the camera is reopened (also after 30 failed reads)
CONFIRM_S = 5.0  # the camera stays on only this long after the receiver last said "recording"
CHECK_EVERY_S = 2.0
REPORT_EVERY_S = 1.0
PROMPT_REPORT_S = 0.25  # a completed fixation goes out this soon after the last report, not a second later
LIVE_EVERY_S = 0.025  # live gaze points while the receiver asks for them: every frame at 30 fps
STAGES = ("camera_wait", "landmarks", "model", "tracking", "network")
DEFAULT_FPS = 30.0
BLINK_SETTLE_S = 0.1  # reject the first reopening frames: iris landmarks still follow the lid
BLINK_HOLD_S = 0.75  # a brief blink preserves filter position; a longer closure starts afresh


class Camera(Protocol):
    def read(self) -> tuple[bool, Any]: ...
    def release(self) -> None: ...


class Estimator(Protocol):
    def extract_features(self, frame: Any) -> tuple[Any, bool]: ...
    def predict(self, features: Any) -> Any: ...


class LatestFrame:
    """The camera opened, read and released on its own thread, keeping only the newest frame.

    ``read`` returns the newest frame not yet returned, waiting up to ``timeout`` for one, so
    frames do not queue up in the driver's buffer while a frame is processed (each queued frame is
    another 33 ms between the eyes moving and the gaze following), and the next frame is captured
    while this one is processed. A failed read is not passed on: webcams often fail their first
    reads while they start, and a read fails only when no frame came for ``timeout``.

    The camera is opened on the reading thread because Windows' camera backends (Media Foundation,
    DirectShow) are COM objects that belong to the thread that created them."""

    def __init__(self, opener: Callable[[], Camera], timeout: float = 1.0, open_timeout: float = 15.0) -> None:
        self.timeout = timeout
        self._opener = opener
        self._ready = threading.Condition()
        self._frame: Any = None
        self._made = self._taken = 0
        self._arrivals: deque[float] = deque(maxlen=1200)  # when frames came, for ``rate``
        self._stop = False
        self._opened = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._read_forever, name="camera", daemon=True)
        self._thread.start()
        if not self._opened.wait(open_timeout):
            self._stop = True
            raise RuntimeError("the camera did not open")
        if self._error is not None:
            raise self._error

    def _read_forever(self) -> None:
        com = _com_start()
        try:
            camera = self._opener()
        except BaseException as error:  # noqa: BLE001 - reported to the opener's caller
            self._error = error
            self._opened.set()
            _com_end(com)
            return
        self._opened.set()
        try:
            while not self._stop:
                ok, frame = camera.read()
                if ok:
                    with self._ready:
                        self._frame, self._made = frame, self._made + 1
                        self._arrivals.append(time.monotonic())
                        self._ready.notify_all()
                else:
                    time.sleep(0.02)
        finally:
            camera.release()
            _com_end(com)

    def rate(self, window: float = 10.0) -> float | None:
        """Frames the camera delivered per second over the last ``window`` seconds (whether or not
        the tracker took them all)."""
        now = time.monotonic()
        with self._ready:
            times = [t for t in self._arrivals if now - t <= window]
        return round((len(times) - 1) / (times[-1] - times[0]), 1) if len(times) > 1 and times[-1] > times[0] else None

    def first(self, seconds: float) -> bool:
        """Whether a frame arrives within ``seconds`` (not taken: ``read`` still returns it)."""
        with self._ready:
            return self._ready.wait_for(lambda: self._made > 0, seconds)

    def read(self) -> tuple[bool, Any]:
        with self._ready:
            self._ready.wait_for(lambda: self._made > self._taken or self._stop, self.timeout)
            if self._made > self._taken:
                self._taken = self._made
                return True, self._frame
            return False, None

    def release(self) -> None:
        self._stop = True
        with self._ready:
            self._ready.notify_all()
        self._thread.join(timeout=2.0)


def _describe(mode: dict | None) -> str:
    from .camera import describe

    return describe(mode) if mode else "default mode"


def _com_start() -> bool:
    """COM for this thread on Windows, where the camera backends are COM objects."""
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        return ctypes.windll.ole32.CoInitializeEx(None, 2) in (0, 1)  # COINIT_APARTMENTTHREADED; S_OK, S_FALSE
    except (AttributeError, OSError):
        return False


def _com_end(started: bool) -> None:
    if started:
        import ctypes

        ctypes.windll.ole32.CoUninitialize()


_threaded_camera = True  # until it fails once on this computer


def open_camera_thread(opener: Callable[[], Camera], first_s: float = 8.0) -> Camera:
    """The camera on its own thread (``LatestFrame``), or, if it will not open or deliver there,
    on the calling thread as before; once that happens the tracker stops trying the thread."""
    global _threaded_camera
    if _threaded_camera:
        try:
            latest = LatestFrame(opener)
            if latest.first(first_s):
                return latest
            latest.release()
            log.warning("the camera gave no frame on its own thread; reading it on the tracker's thread")
        except Exception as error:  # noqa: BLE001 - fall back to the old way below
            log.warning("the camera would not open on its own thread (%s); opening it on the tracker's thread", error)
        _threaded_camera = False
    return opener()


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
        overlay: Callable[[], Any] | None = None,
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
        self._blink_started: float | None = None
        self._blink_until = -1e9
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
        self.frame_at = 0.0  # when the camera last gave a frame (or was opened)
        self.retry_at = 0.0
        self.checked_at = -1e9
        self.reported_at = -1e9
        self.pending = False  # fixations completed since the last report
        self.live_at = -1e9
        self.offline_since: float | None = None
        self.last_face = clock()
        self.frames: deque[tuple[float, bool]] = deque()  # (time, had a face) over the last 10 s
        self.samples: deque[float] = deque()  # gaze samples given to the detector over the last 30 s
        self.fixated: deque[tuple[float, int]] = deque()  # (time, samples) of fixations over the last 30 s
        self.heads: deque[tuple[float, float, float]] = deque()  # (time, mm, degrees) from calibration, 10 s
        self.timing: deque[tuple[float, ...]] = deque()  # (time, ms per stage...) of frames with a face, 10 s
        # The ring drawn over every window (gaze.overlay), made when the receiver first asks for it.
        self._overlay_factory = overlay
        self.overlay: Any = None

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
            "model": self.calibration.get("model"),
            # Where the head is against where it was while calibrating (head-aware model only).
            "head_moved_mm": round(float(np.median([m for _, m, _ in self.heads])), 0) if self.heads else None,
            "head_turned_deg": round(float(np.median([d for _, _, d in self.heads])), 1) if self.heads else None,
            "timing_ms": self._timing(),
            "overlay": self.overlay.state() if self.overlay is not None else None,
            "camera_fps": self.camera.rate() if hasattr(self.camera, "rate") else None,
            "camera_mode": _describe(self.calibration.get("camera_mode")),
        }

    def _timing(self) -> dict | None:
        """Median milliseconds per frame in each stage over the last 10 s, the frame's processing
        in all (camera wait excluded), and a live point's round trip on its own thread."""
        if not self.timing:
            return None
        rows = np.array([row[1:] for row in self.timing])
        out = {name: round(float(np.median(rows[:, i])), 2) for i, name in enumerate(STAGES)}
        out["frame"] = round(float(np.median(rows[:, 1:].sum(1))), 2)
        live = getattr(self.receiver, "live_ms", None)
        if live:
            out["live_post"] = round(float(np.median(list(live))), 2)
        return out

    def _open(self) -> int:
        """Samples in the fixation still under way, once it is long enough to count."""
        window = self.detector.window
        return len(window) if window and window[-1][0] - window[0][0] >= self.detector.min_duration else 0

    def _fixations(self, now: float, found: list) -> None:
        for fixation in found:
            self.receiver.add(fixation)
            self.fixated.append((now, fixation.samples))
            self.pending = True
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

    def _ring(self) -> Any:
        """The overlay to draw on, or None: made on first need, hidden while the receiver does not
        want the ring (switched off, paused, not answering)."""
        if not getattr(self.receiver, "ring", False):
            if self.overlay is not None:
                self.overlay.clear()
            return None
        if self.overlay is None and self._overlay_factory is not None:
            try:
                self.overlay = self._overlay_factory()
            except Exception as error:  # noqa: BLE001 - tracking goes on without the ring
                log.warning("the gaze ring overlay could not start: %s", error)
            self._overlay_factory = None  # one attempt per run: its state says why it failed
        return self.overlay if self.overlay is not None and self.overlay.running else None

    def _release(self) -> None:
        if self.overlay is not None:
            self.overlay.clear()
        if self.camera is not None:
            self.camera.release()
            self.camera = None
        for fixation in self.detector.flush():
            self.receiver.add(fixation)
        self.smoother.reset()
        self.frames.clear()
        self.samples.clear()
        self.fixated.clear()
        self.heads.clear()
        self.timing.clear()
        self._prediction_input = None
        self._blink_started, self._blink_until = None, -1e9
        release_buffers = getattr(self.estimator, "release_frame_buffers", None)
        if release_buffers is not None:
            release_buffers()

    def _report(self, now: float) -> None:
        since = now - self.reported_at
        if since < REPORT_EVERY_S and not (self.pending and since >= PROMPT_REPORT_S):
            return
        self.reported_at, self.pending = now, False
        result = self.receiver.send(self.state())
        self._ring()  # the ring switched off or on: act now, not at the next face
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
                why = getattr(self.receiver, "refusal", None)
                log.warning("the receiver refused gaze data: %s", why or "vault token?")

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
                self.failures, self.problem, self.frame_at = 0, None, now
            except Exception as error:  # noqa: BLE001 - any camera failure means try again later
                log.warning("camera unavailable: %s", error)
                self.problem, self.retry_at = "camera unavailable", now + 10
                return
        waited = self.timer()
        ok, frame = self.camera.read()
        read = self.timer()
        if not ok:
            self.failures += 1
            if self.failures >= 30 or now - self.frame_at > CAMERA_STALL_S:  # unplugged, or lost across sleep: reopen
                log.warning("camera stopped delivering frames; reopening")
                self._release()
                self.problem, self.retry_at = "camera stopped", now + 5
            self._report(now)
            return
        self.failures, self.frame_at = 0, now
        features, blink = self.estimator.extract_features(frame)
        extracted = self.timer()
        face = features is not None
        self.frames.append((started, face))
        while self.frames and started - self.frames[0][0] > 10:
            self.frames.popleft()
        if face:
            self.last_face = now
            moved = getattr(self.estimator, "moved", None)
            where = moved() if moved is not None else None
            if where is not None:
                self.heads.append((started, *where))
                while started - self.heads[0][0] > 10:
                    self.heads.popleft()
        predicted = tracked = extracted
        if blink:
            if self._blink_started is None:
                self._blink_started = started
            self._blink_until = started + BLINK_SETTLE_S
        if face and not blink and started >= self._blink_until:
            if self._blink_started is not None:
                if started - self._blink_started <= BLINK_HOLD_S:
                    self.smoother.resume(now, self.period)
                else:
                    self.smoother.reset()
                self._blink_started = None
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
            predicted = self.timer()
            # A gaze ray that never meets the screen's plane (a bad frame's landmarks) is nan,
            # which must not reach the smoother: its state would stay nan.
            if math.isfinite(x) and math.isfinite(y):
                self._gaze(now, x, y)
            tracked = self.timer()
        self._report(now)
        if face:
            self._time(started, waited, read, extracted, predicted, tracked, self.timer())
        self.sleep(max(0.0, self.period - (self.timer() - started)))

    def _time(self, started: float, *marks: float) -> None:
        waited, read, extracted, predicted, tracked, reported = marks
        split = getattr(self.estimator, "timing", None)  # (landmarks, model) ms, when the estimator measures it
        landmarks, model = split if split else ((extracted - read) * 1000, 0.0)
        model += (predicted - extracted) * 1000
        self.timing.append(
            (
                started,
                (read - waited) * 1000,
                landmarks,
                model,
                (tracked - predicted) * 1000,
                (reported - tracked) * 1000,
            )
        )
        while started - self.timing[0][0] > 10:
            self.timing.popleft()

    def _gaze(self, now: float, x: float, y: float) -> None:
        previous = self.smoother.x.t
        if previous is not None and now - previous > self.detector.max_gap:
            self.smoother.reset()  # a lost face/ray breaks continuity just as it does for fixations
        x, y = self.smoother(now, x, y)
        fx, fy = x / self.width, y / self.height
        ring = self._ring()
        if ring is not None:  # in memory, every frame; off the monitor it waits at the edge
            ring.push(*corrected(getattr(self.receiver, "offset_grid", None), fx, fy))
        if -0.05 <= fx <= 1.05 and -0.05 <= fy <= 1.05:  # off-screen samples end the fixation
            if getattr(self.receiver, "live", False) and now - self.live_at >= LIVE_EVERY_S:
                self.live_at = now
                self.receiver.send_live(now, fx, fy)
            self.samples.append(now)
            while now - self.samples[0] > 30:
                self.samples.popleft()
            self._fixations(now, self.detector.add(now, fx, fy))
        else:
            self._fixations(now, self.detector.flush())

    def run(self, should_stop: Callable[[], bool] = lambda: False) -> None:
        try:
            while not should_stop():
                self.step()
        finally:
            self._release()
            if self.overlay is not None:
                self.overlay.close()
            self.hold = "stopped"
            self.reported_at = -1e9
            with contextlib.suppress(SystemExit):
                self._report(self.clock())

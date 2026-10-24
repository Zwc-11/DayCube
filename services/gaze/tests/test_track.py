"""The tracking loop with a scripted camera, estimator, receiver, clock and computer."""

from dataclasses import dataclass, field

import pytest
from gaze.track import Tracker

CALIBRATION = {"monitor": [1920, 1080], "error_px": 90.0, "camera": 0, "model": "ridge"}


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.001)


@dataclass
class Scene:
    """Where the person looks; None for no face."""

    point: tuple[float, float] | None = (960.0, 540.0)


class Camera:
    def __init__(self, scene: Scene, log: list) -> None:
        self.scene, self.log = scene, log
        log.append("open")

    def read(self) -> tuple[bool, object]:
        return True, self.scene.point

    def release(self) -> None:
        self.log.append("release")


class Estimator:
    def extract_features(self, frame: object) -> tuple[object, bool]:
        return (list(frame) if frame is not None else None), False

    def predict(self, features: object) -> object:
        return features


@dataclass
class Receiver:
    answer: str = "ok"
    fixations: list = field(default_factory=list)
    states: list = field(default_factory=list)
    live: bool = False
    ring: bool = False
    offset_grid: dict | None = None
    points: list = field(default_factory=list)
    sent: list = field(default_factory=list)  # how many fixations each report carried

    def add(self, fixation: object) -> None:
        self.fixations.append(fixation)

    def send(self, state: dict) -> str:
        self.states.append(state)
        self.sent.append(len(self.fixations))
        return self.answer

    def send_live(self, t: float, x: float, y: float) -> None:
        self.points.append((t, x, y))


@dataclass
class Computer:
    idle: float = 0.0
    is_locked: bool = False
    busy: bool = False

    def idle_seconds(self) -> float:
        return self.idle

    def locked(self) -> bool:
        return self.is_locked

    def camera_busy(self) -> bool:
        return self.busy


def tracker(**options: object) -> tuple[Tracker, Scene, Receiver, Computer, Clock, list]:
    scene, receiver, computer, clock, log = Scene(), Receiver(), Computer(), Clock(), []
    t = Tracker(
        Estimator(), lambda: Camera(scene, log), receiver, CALIBRATION, system=computer, clock=clock,
        sleep=clock.sleep, timer=clock, **options,
    )  # fmt: skip
    return t, scene, receiver, computer, clock, log


def run_for(t: Tracker, clock: Clock, seconds: float) -> None:
    end = clock.now + seconds
    while clock.now < end:
        t.step()


def test_a_steady_look_becomes_fixations_at_its_screen_fraction() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    run_for(t, clock, 4)
    assert len(receiver.fixations) == 3
    assert all(abs(f.x - 0.5) < 1e-6 and abs(f.y - 0.5) < 1e-6 for f in receiver.fixations)
    state = receiver.states[-1]
    assert state["tracking"] is True and state["face"] == 1.0 and 29 <= state["fps"] <= 30.5
    assert state["target_fps"] == 30.0
    assert state["coverage"] >= 0.9 and state["noise_px"] is not None and state["noise_px"] < 1  # a still point
    assert log == ["open"]


def test_a_pause_at_the_receiver_turns_the_camera_off_until_resumed() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    run_for(t, clock, 1.5)
    receiver.answer = "paused"
    run_for(t, clock, 3)
    assert log == ["open", "release"] and receiver.states[-1]["reason"] == "paused"
    receiver.answer = "ok"
    run_for(t, clock, 3)
    assert log == ["open", "release", "open"] and receiver.states[-1]["tracking"] is True


def test_locking_or_a_video_call_releases_the_camera() -> None:
    t, scene, receiver, computer, clock, log = tracker()
    run_for(t, clock, 1)
    computer.is_locked = True
    run_for(t, clock, 3)
    assert log[-1] == "release" and receiver.states[-1]["reason"] == "locked"
    computer.is_locked, computer.busy = False, True
    run_for(t, clock, 3)
    assert log[-1] == "release" and receiver.states[-1]["reason"] == "camera busy"
    computer.busy = False
    run_for(t, clock, 3)
    assert log[-1] == "open"


def test_away_needs_both_no_input_and_no_face() -> None:
    t, scene, receiver, computer, clock, log = tracker()
    computer.idle = 600  # reading without touching anything
    run_for(t, clock, 90)
    assert log == ["open"]
    scene.point = None  # walked away
    run_for(t, clock, 65)
    assert log == ["open", "release"] and receiver.states[-1]["reason"] == "away"
    scene.point = (960.0, 540.0)
    run_for(t, clock, 10)
    assert log == ["open", "release"]  # a face is not enough while away: the camera is off
    computer.idle = 1  # back at the keyboard
    run_for(t, clock, 3)
    assert log == ["open", "release", "open"]


def test_the_camera_stays_off_until_the_receiver_confirms_recording() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    receiver.answer = "paused"  # a pause remembered from before sign-in
    run_for(t, clock, 10)
    assert log == [] and receiver.states[-1]["reason"] == "paused"
    receiver.answer = "offline"  # a stopped receiver is not a resume
    run_for(t, clock, 10)
    assert log == []
    receiver.answer = "ok"
    run_for(t, clock, 1)
    assert log == ["open"]


def test_a_receiver_that_stops_answering_turns_the_camera_off() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    run_for(t, clock, 2)
    receiver.answer = "offline"
    run_for(t, clock, 3)
    assert log == ["open"]  # a brief hiccup is tolerated
    run_for(t, clock, 5)
    assert log == ["open", "release"] and receiver.states[-1]["reason"] == "receiver not running"


def test_a_supervised_tracker_exits_when_the_receiver_is_gone() -> None:
    t, scene, receiver, *_, clock, log = tracker(supervised=True)
    receiver.answer = "offline"
    with pytest.raises(SystemExit):
        run_for(t, clock, 200)
    assert receiver.states[-1]["reason"] == "receiver not running"


def test_a_missing_camera_is_retried() -> None:
    attempts = []

    def broken() -> Camera:
        attempts.append(1)
        raise RuntimeError("cannot open camera 0")

    t, scene, receiver, computer, clock, log = tracker()
    t.open_camera = broken
    run_for(t, clock, 25)
    assert len(attempts) == 3 and receiver.states[-1]["reason"] == "camera unavailable"


def test_a_supervised_tracker_restarts_for_a_new_calibration() -> None:
    changed = []
    t, scene, receiver, *_, clock, log = tracker(supervised=True, recalibrated=lambda: bool(changed))
    run_for(t, clock, 3)
    changed.append(True)
    with pytest.raises(SystemExit):
        run_for(t, clock, 3)


@pytest.mark.parametrize("fps", [15.0, 30.0])
def test_configured_target_controls_pacing_without_changing_positions(fps: float) -> None:
    t, scene, receiver, *_, clock, log = tracker(fps=fps)
    run_for(t, clock, 4)
    assert abs(t.state()["fps"] - fps) < 0.1
    assert t.state()["target_fps"] == fps
    assert len(receiver.fixations) == 3
    assert all((fixation.x, fixation.y) == (0.5, 0.5) for fixation in receiver.fixations)


@pytest.mark.parametrize("fps", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_rate_fails_before_opening_camera(fps: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        tracker(fps=fps)


def test_prediction_row_is_reused_and_released_with_camera() -> None:
    import numpy as np

    class InspectEstimator(Estimator):
        def __init__(self) -> None:
            self.rows = []
            self.values = []

        def predict(self, features: np.ndarray) -> np.ndarray:
            self.rows.append(features)
            self.values.append(features.copy())
            return features

    t, scene, receiver, *_, clock, log = tracker()
    estimator = InspectEstimator()
    t.estimator = estimator
    run_for(t, clock, 1.5)
    assert len(estimator.rows) > 10
    assert all(row is estimator.rows[0] for row in estimator.rows)
    assert all(np.array_equal(row, [[960.0, 540.0]]) for row in estimator.values)
    scene.point = (800.0, 400.0)
    run_for(t, clock, 0.5)
    assert np.array_equal(estimator.values[-1], [[800.0, 400.0]])
    assert estimator.rows[-1] is estimator.rows[0]
    t._release()
    assert t._prediction_input is None and log[-1] == "release"


def test_fps_and_pacing_do_not_follow_wall_clock_corrections() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    run_for(t, clock, 1)
    t.clock = lambda: clock() - 100.0
    run_for(t, clock, 2)
    assert 29 <= t.state()["fps"] <= 30.5


def test_live_points_stream_every_frame_only_while_asked() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    run_for(t, clock, 3)  # the camera is on after the receiver's first confirmation
    assert receiver.points == [] and receiver.fixations
    receiver.live = True
    start = clock.now
    run_for(t, clock, 2)
    assert 58 <= len(receiver.points) <= 62  # 30 frames/s
    assert all(abs(x - 0.5) < 1e-6 and abs(y - 0.5) < 1e-6 for _, x, y in receiver.points)
    assert receiver.points[0][0] >= start


def test_frame_time_is_reported_by_stage() -> None:
    class Timed(Estimator):
        timing = (12.5, 0.2)

    t, scene, receiver, *_, clock, log = tracker()
    t.estimator = Timed()
    run_for(t, clock, 2)
    timing = receiver.states[-1]["timing_ms"]
    assert set(timing) == {"camera_wait", "landmarks", "model", "tracking", "network", "frame"}
    assert timing["landmarks"] == 12.5 and timing["model"] == 0.2  # the scripted clock adds no time
    assert timing["frame"] == 12.7


def test_the_newest_camera_frame_is_read_and_none_twice() -> None:
    import threading
    import time as real_time

    from gaze.track import LatestFrame

    threads: set[str] = set()

    class Camera:
        def __init__(self) -> None:
            threads.add(threading.current_thread().name)
            self.n, self.released = 0, False

        def read(self) -> tuple[bool, int]:
            threads.add(threading.current_thread().name)
            real_time.sleep(0.005)
            self.n += 1
            if self.n <= 5:  # a webcam that fails its first reads while it starts
                return False, None
            return True, self.n

        def release(self) -> None:
            threads.add(threading.current_thread().name)
            self.released = True

    cameras: list[Camera] = []
    latest = LatestFrame(lambda: cameras.append(Camera()) or cameras[-1])
    ok, first = latest.read()
    assert ok and first > 5  # the failed start is not passed on
    real_time.sleep(0.05)  # frames keep coming while this one is "processed"
    ok, second = latest.read()
    assert ok and second > first + 3  # the newest, not the next in a queue
    ok, third = latest.read()
    assert ok and third > second  # never the same frame twice
    latest.release()
    assert cameras[0].released and not any(t.name == "camera" and t.is_alive() for t in threading.enumerate())
    assert threads == {"camera"}  # opened, read and released on its own thread (Windows COM)


def test_a_camera_that_stops_delivering_fails_reads() -> None:
    from gaze.track import LatestFrame

    class Dead:
        def read(self) -> tuple[bool, None]:
            return False, None

        def release(self) -> None:
            pass

    latest = LatestFrame(Dead, timeout=0.2)
    assert latest.read() == (False, None)
    latest.release()


def test_a_camera_that_cannot_open_says_so() -> None:
    from gaze.track import LatestFrame

    def broken() -> None:
        raise RuntimeError("cannot open camera 0")

    with pytest.raises(RuntimeError, match="cannot open camera 0"):
        LatestFrame(broken)


def test_a_completed_fixation_is_reported_within_a_quarter_second() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    run_for(t, clock, 3.2)  # camera on, a long look split into fixations of a second
    assert receiver.fixations
    reported = len(receiver.states)
    before = len(receiver.fixations)
    scene.point = (300.0, 200.0)  # a saccade: the fixation under way ends now
    ended = clock.now
    while len(receiver.fixations) == before:
        t.step()
    completed = clock.now
    while receiver.sent[-1] < len(receiver.fixations):
        t.step()
    assert clock.now - completed <= 0.3 and len(receiver.states) > reported
    assert completed - ended < 0.3  # the smoother follows the saccade within a few frames


def test_head_position_since_calibration_is_reported() -> None:
    class HeadAware(Estimator):
        def moved(self) -> tuple[float, float]:
            return 52.0, 7.5

    t, scene, receiver, *_, clock, log = tracker()
    t.estimator = HeadAware()
    t.calibration = {**CALIBRATION, "model": "head"}
    run_for(t, clock, 2)
    state = receiver.states[-1]
    assert state["model"] == "head" and state["head_moved_mm"] == 52 and state["head_turned_deg"] == 7.5
    scene.point = None  # no face: the last ten seconds still describe the head
    run_for(t, clock, 1)
    assert receiver.states[-1]["head_moved_mm"] == 52


def test_an_eyetrax_calibration_reports_no_head_position() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    run_for(t, clock, 2)
    assert receiver.states[-1]["model"] == "ridge" and receiver.states[-1]["head_moved_mm"] is None


def test_a_gaze_ray_that_misses_the_screen_is_skipped() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    run_for(t, clock, 2)
    scene.point = (float("nan"), float("nan"))
    run_for(t, clock, 0.5)
    scene.point = (960.0, 540.0)
    run_for(t, clock, 2)
    assert t.smoother.x.value == 960.0
    assert all(abs(f.x - 0.5) < 1e-6 for f in receiver.fixations)


def test_reacquiring_a_face_starts_at_its_new_gaze_point() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    receiver.live = True
    run_for(t, clock, 2)
    scene.point = None
    run_for(t, clock, 0.6)
    scene.point = (250.0, 200.0)
    t.step()
    _, x, y = receiver.points[-1]
    assert x == pytest.approx(250 / 1920, abs=1e-12)
    assert y == pytest.approx(200 / 1080, abs=1e-12)


def test_a_short_blink_keeps_the_continuous_smoothing_response() -> None:
    from gaze.fixations import Smoother

    t, scene, receiver, *_, clock, log = tracker()
    receiver.live = True
    expected = Smoother()
    start = clock.now
    for elapsed, x, y in [(0.0, 960.0, 540.0), (0.03, 1000.0, 550.0), (0.13, 1400.0, 800.0)]:
        now = start + elapsed
        ex, ey = expected(now, x, y)
        t._gaze(now, x, y)
        assert receiver.points[-1][1:] == pytest.approx((ex / 1920, ey / 1080))


def test_releasing_the_camera_also_releases_estimator_pixel_scratch() -> None:
    t, scene, receiver, *_, clock, log = tracker()

    class WithScratch(Estimator):
        def release_frame_buffers(self) -> None:
            assert t.camera is None
            log.append("pixels released")

    t.estimator = WithScratch()
    run_for(t, clock, 1)
    t._release()
    assert log[-2:] == ["release", "pixels released"]


def test_looking_off_screen_ends_the_open_fixation() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    run_for(t, clock, 0.9)
    assert t.detector.window and not receiver.fixations
    last_sample = t.detector.window[-1][0]
    t._gaze(clock.now, 1e6, 1e6)  # a finite ray that lands far beyond the monitor
    assert t.detector.window == []
    assert len(receiver.fixations) == 1 and receiver.fixations[0].end == last_sample
    assert receiver.fixations[0].x == pytest.approx(0.5)


def test_a_webcam_slow_to_start_stays_open() -> None:
    """The real loop, real time, with the threaded reader: a webcam whose first reads fail at once
    (as many do while starting) is not reopened over and over; it is opened once and tracked."""
    import time as real_time

    from gaze.track import LatestFrame

    opened: list[int] = []

    class Webcam:
        def __init__(self) -> None:
            opened.append(1)
            self.reads = 0

        def read(self) -> tuple[bool, object]:
            self.reads += 1
            if self.reads <= 40:
                return False, None  # fails instantly while starting
            real_time.sleep(1 / 30)
            return True, (960.0, 540.0)

        def release(self) -> None:
            pass

    receiver = Receiver()
    t = Tracker(Estimator(), lambda: LatestFrame(Webcam), receiver, CALIBRATION, system=Computer())
    end = real_time.monotonic() + 3
    t.run(lambda: real_time.monotonic() > end)
    assert len(opened) == 1
    assert receiver.fixations and receiver.states[-2]["tracking"] is True


def test_a_camera_that_works_only_on_the_tracker_thread_is_still_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Should a camera backend refuse another thread, the tracker falls back to reading it on its
    own thread, as before, and stops trying the reader thread."""
    import threading

    from gaze import track

    monkeypatch.setattr(track, "_threaded_camera", True)

    class Picky:
        def __init__(self) -> None:
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError("cannot open camera 0")
            self.thread = threading.current_thread()

        def read(self) -> tuple[bool, object]:
            return True, (1.0, 2.0)

        def release(self) -> None:
            pass

    camera = track.open_camera_thread(Picky)
    assert isinstance(camera, Picky) and camera.read() == (True, (1.0, 2.0))
    assert track._threaded_camera is False
    assert isinstance(track.open_camera_thread(Picky), Picky)  # no second try on the thread

    class Silent:  # opens on the thread but never delivers there
        def __init__(self) -> None:
            self.main = threading.current_thread() is threading.main_thread()

        def read(self) -> tuple[bool, object]:
            import time

            time.sleep(0.01)
            return (True, "frame") if self.main else (False, None)

        def release(self) -> None:
            pass

    monkeypatch.setattr(track, "_threaded_camera", True)
    camera = track.open_camera_thread(Silent, first_s=0.3)
    assert isinstance(camera, Silent) and camera.main


class FakeRing:
    """Stands in for gaze.overlay.Overlay."""

    def __init__(self, running: bool = True) -> None:
        self.running = running
        self.points: list[tuple[float, float]] = []
        self.cleared = 0
        self.closed = False

    def push(self, fx: float, fy: float) -> None:
        self.points.append((fx, fy))

    def clear(self) -> None:
        self.cleared += 1

    def close(self) -> None:
        self.closed = True

    def state(self) -> dict:
        return {"running": self.running, "shown": bool(self.points), "fps": 144, "refresh_hz": 144.0}


def test_the_ring_over_every_window_gets_every_frame_in_memory() -> None:
    made: list[FakeRing] = []

    def factory() -> FakeRing:
        made.append(FakeRing())
        return made[-1]

    t, scene, receiver, *_, clock, log = tracker(overlay=factory)
    receiver.live = True  # a page asks too: it still gets live points over HTTP
    run_for(t, clock, 3)
    assert made == []  # not wanted yet: no window is made
    receiver.ring = True
    # The receiver's click correction: the tracker reads 2% of the width too far right everywhere.
    receiver.offset_grid = {"cols": 2, "rows": 2, "dx": [0.02] * 4, "dy": [0.0] * 4}
    run_for(t, clock, 2)
    ring = made[0]
    assert len(made) == 1 and 58 <= len(ring.points) <= 62  # every frame, at 30 frames/s
    assert all(abs(x - 0.48) < 1e-6 and abs(y - 0.5) < 1e-6 for x, y in ring.points)
    assert t.state()["overlay"]["running"] and receiver.states[-1]["overlay"]["fps"] == 144
    receiver.answer = "paused"  # a pause hides it and releases the camera
    run_for(t, clock, 3)
    assert ring.cleared and "release" in log
    t.run(should_stop=lambda: True)
    assert ring.closed


def test_a_ring_window_that_cannot_open_leaves_tracking_alone() -> None:
    t, scene, receiver, *_, clock, log = tracker(overlay=lambda: FakeRing(running=False))
    receiver.ring = True
    run_for(t, clock, 3)
    assert receiver.fixations and t.state()["overlay"]["running"] is False


def test_the_click_correction_is_interpolated_between_grid_points() -> None:
    from gaze.receiver import _grid, corrected

    grid = _grid({"cols": 3, "rows": 2, "dx": [0, 0.01, 0.02, 0, 0.01, 0.02], "dy": [0, 0, 0, 0.04, 0.04, 0.04]})
    assert corrected(grid, 0.25, 0.5) == pytest.approx((0.25 - 0.005, 0.5 - 0.02))
    assert corrected(grid, 1.3, 1.0) == pytest.approx((1.3 - 0.02, 1.0 - 0.04))  # clamped to the grid's edge
    assert corrected(None, 0.3, 0.4) == (0.3, 0.4)
    assert _grid({"cols": 3, "rows": 2, "dx": [0] * 5, "dy": [0] * 6}) is None
    assert _grid({"cols": 3, "rows": 2, "dx": ["x"] * 6, "dy": [0] * 6}) is None
    assert _grid(None) is None


def test_clicks_and_the_frames_before_them_go_to_the_learner() -> None:
    class Learner:
        def __init__(self) -> None:
            self.frames: list = []
            self.clicks: list = []

        def frame(self, t: float, vector: object, x: float, y: float) -> None:
            self.frames.append((t, x, y))

        def click(self, t: float, x: float, y: float) -> str:
            self.clicks.append((t, x, y, len(self.frames)))
            return "used"

        def state(self) -> dict:
            return {"version": 2, "clicks": len(self.clicks)}

    class Clicking(Computer):
        presses: list = []

        def click(self) -> tuple[int, int] | None:
            return self.presses.pop(0) if self.presses else None

    learner = Learner()
    scene, receiver, clock, log = Scene(), Receiver(), Clock(), []
    computer = Clicking()
    t = Tracker(
        Estimator(), lambda: Camera(scene, log), receiver, CALIBRATION, system=computer, clock=clock,
        sleep=clock.sleep, timer=clock, learner=learner,
    )  # fmt: skip
    run_for(t, clock, 1)
    computer.presses.append((960, 540))
    run_for(t, clock, 1.5)
    assert len(learner.frames) >= 40 and learner.frames[0][1:] == (960.0, 540.0)
    ((when, x, y, seen),) = learner.clicks
    assert (x, y) == (960.0, 540.0) and seen >= 10  # the frames before it were there
    assert receiver.states[-1]["learning"] == {"version": 2, "clicks": 1}
    # A learner that fails must not stop tracking.
    learner.click = lambda *_: 1 / 0  # type: ignore[method-assign]
    computer.presses.append((10, 10))
    run_for(t, clock, 0.5)
    assert receiver.states[-1]["tracking"] is True

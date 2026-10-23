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

    def add(self, fixation: object) -> None:
        self.fixations.append(fixation)

    def send(self, state: dict) -> str:
        self.states.append(state)
        return self.answer


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

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
        sleep=clock.sleep, **options,
    )  # fmt: skip
    return t, scene, receiver, computer, clock, log


def run_for(t: Tracker, clock: Clock, seconds: float) -> None:
    end = clock.now + seconds
    while clock.now < end:
        t.step()


def test_a_steady_look_becomes_fixations_at_its_screen_fraction() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    run_for(t, clock, 3.5)
    assert len(receiver.fixations) == 3
    assert all(abs(f.x - 0.5) < 1e-6 and abs(f.y - 0.5) < 1e-6 for f in receiver.fixations)
    state = receiver.states[-1]
    assert state["tracking"] is True and state["face"] == 1.0 and 14 <= state["fps"] <= 15.5
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

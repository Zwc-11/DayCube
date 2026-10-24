"""Blink transitions and live-marker stability, using synthetic eyelids and scripted time."""

import numpy as np
import pytest
from gaze.face import LEFT_EYE, RIGHT_EYE
from gaze.head import Blinks
from test_track import Estimator, run_for, tracker


def eyes(right: float = 0.3, left: float = 0.3) -> np.ndarray:
    points = np.zeros((478, 3))
    for indices, aperture in [(RIGHT_EYE, right), (LEFT_EYE, left)]:
        points[list(indices), :2] = [(0, 0), (100, 0), (50, -50 * aperture), (50, 50 * aperture)]
    return points


def warmed() -> Blinks:
    detector = Blinks()
    for _ in range(30):
        assert not detector(eyes())
    return detector


def test_closing_lids_are_rejected_before_the_iris_disappears() -> None:
    detector = warmed()
    assert detector(eyes(0.264, 0.264))  # 12% closure, before the old 20% threshold


@pytest.mark.parametrize("right,left", [(0.21, 0.3), (0.3, 0.21)])
def test_one_closed_eye_is_not_hidden_by_the_other(right: float, left: float) -> None:
    assert warmed()(eyes(right, left))


def test_closed_frames_never_become_the_new_open_eye_baseline() -> None:
    detector = warmed()
    assert all(detector(eyes(0.01, 0.01)) for _ in range(200))
    assert not detector(eyes())


def test_gentle_aperture_changes_and_narrow_open_eyes_are_allowed() -> None:
    detector = warmed()
    assert not any(detector(eyes(value, value)) for value in np.linspace(0.3, 0.27, 30))
    narrow = Blinks()
    decisions = [narrow(eyes(0.15, 0.17)) for _ in range(30)]
    assert not any(decisions[-10:])


def test_starting_closed_or_with_missing_landmarks_stays_suppressed() -> None:
    detector = Blinks()
    assert all(detector(eyes(0.01, 0.01)) for _ in range(90))
    invalid = eyes()
    invalid[RIGHT_EYE[2], 1] = np.nan
    assert detector(invalid)


class BlinkingEstimator(Estimator):
    def __init__(self) -> None:
        self.apertures = (0.3, 0.3)
        self.blinks = Blinks()
        self.predictions = 0

    def extract_features(self, frame: object) -> tuple[object, bool]:
        return list(frame), self.blinks(eyes(*self.apertures))

    def predict(self, features: object) -> object:
        self.predictions += 1
        return features


def test_partial_blink_and_reopening_do_not_move_the_live_circle() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    estimator = BlinkingEstimator()
    t.estimator, receiver.live = estimator, True
    run_for(t, clock, 2)
    before = len(receiver.points)
    # Corrupted iris positions during closing and the first reopening frames. The closed-frame
    # flag alone misses the 88%, 85% and 95% frames, which made the marker dip then return.
    for aperture, y in [(0.88, 950), (0.5, 1000), (0.03, 1000), (0.03, 1000), (0.7, 950), (0.85, 950), (0.95, 850)]:
        estimator.apertures = (0.3 * aperture, 0.3 * aperture)
        scene.point = (960.0, float(y))
        t.step()
    assert len(receiver.points) == before
    estimator.apertures, scene.point = (0.3, 0.3), (960.0, 540.0)
    run_for(t, clock, 0.4)
    assert len(receiver.points) > before
    assert all(y == pytest.approx(0.5) for _, _, y in receiver.points)


def test_long_eye_closure_does_not_emit_held_positions_and_can_reacquire_elsewhere() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    estimator = BlinkingEstimator()
    t.estimator, receiver.live = estimator, True
    run_for(t, clock, 2)
    before = len(receiver.points)
    estimator.apertures, scene.point = (0.01, 0.01), (960.0, 1000.0)
    run_for(t, clock, 3)
    assert len(receiver.points) == before
    estimator.apertures, scene.point = (0.3, 0.3), (250.0, 200.0)
    run_for(t, clock, 0.3)
    assert len(receiver.points) > before
    assert receiver.points[-1][1:] == pytest.approx((250 / 1920, 200 / 1080))


@pytest.mark.parametrize("fps", [15.0, 30.0])
def test_reopening_waits_by_time_and_retains_filter_stability(fps: float) -> None:
    from gaze.track import BLINK_SETTLE_S

    t, scene, receiver, *_, clock, log = tracker(fps=fps)
    estimator = BlinkingEstimator()
    t.estimator, receiver.live = estimator, True
    run_for(t, clock, 3)
    estimator.apertures = (0.01, 0.01)
    run_for(t, clock, 0.15)
    last_closed = clock.now - t.period
    estimator.apertures, scene.point = (0.3, 0.3), (960.0, 580.0)  # small normal jitter after opening
    before = len(receiver.points)
    for _ in range(10):
        t.step()
        if len(receiver.points) > before:
            break
    first_t, _, y = receiver.points[before]
    assert first_t - last_closed >= BLINK_SETTLE_S - 1e-6
    assert 540 <= y * 1080 < 560  # blink time must not turn one noisy sample into a raw-position jump


def test_camera_release_clears_the_blink_recovery_state() -> None:
    t, scene, receiver, *_, clock, log = tracker()
    estimator = BlinkingEstimator()
    t.estimator, receiver.live = estimator, True
    run_for(t, clock, 2)
    estimator.apertures = (0.01, 0.01)
    t.step()
    t._release()
    estimator.apertures = (0.3, 0.3)
    before = len(receiver.points)
    t.step()
    assert len(receiver.points) == before + 1

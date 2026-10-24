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


def test_desktop_overlay_is_guarded_before_it_receives_a_blink_point() -> None:
    from test_track import FakeRing

    ring = FakeRing()
    t, scene, receiver, *_, clock, log = tracker(overlay=lambda: ring)
    estimator = BlinkingEstimator()
    t.estimator, receiver.ring = estimator, True
    run_for(t, clock, 2)
    before = len(ring.points)
    for aperture, y in [(0.88, 950), (0.5, 1000), (0.03, 1000), (0.7, 950), (0.85, 950), (0.95, 850)]:
        estimator.apertures, scene.point = (0.3 * aperture, 0.3 * aperture), (960.0, float(y))
        t.step()
    assert len(ring.points) == before
    estimator.apertures, scene.point = (0.3, 0.3), (960.0, 540.0)
    run_for(t, clock, 0.4)
    assert len(ring.points) > before and all(y == pytest.approx(0.5) for _, y in ring.points)


# -- looking down is not blinking --------------------------------------------------------------------


def looking(v: float, aperture: float | None = None) -> np.ndarray:
    """Open eyes with the iris ``v`` (share of the eye's width) below the corners' line. The lid
    follows the eye down: the gap narrows by about a third of the iris's drop (MPIIFaceGaze's
    median slope), so the bottom of a large monitor (v near 0.12) is 25% narrower than the top."""
    from gaze.face import LEFT_IRIS, RIGHT_IRIS

    gap = 0.3 - 0.6 * (v - (-0.02)) if aperture is None else aperture
    points = eyes(gap, gap)
    for iris in (RIGHT_IRIS, LEFT_IRIS):  # both eyes' corners are at (0, 0) and (100, 0)
        points[iris, :2] = [50.0, 100.0 * v]
    return points


class Ticks:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_an_open_eye_looking_at_the_bottom_of_the_screen_is_not_a_blink() -> None:
    clock = Ticks()
    detector = Blinks(clock=clock)
    for k in range(300):  # ten seconds of reading the top two thirds, the eyes drifting up and down
        clock.now += 1 / 30
        assert not detector(looking(0.02 + 0.04 * np.sin(2 * np.pi * k / 120)))
    # Down to the bottom in one saccade: the gap is 0.22 against a 0.27 average, under 80%, and it
    # fell 18% in one frame. The reference follows the iris, and the fall is the iris's own move,
    # so no frame is held.
    assert detector.expected(0, 0.12) < 0.25
    decisions = []
    for _ in range(30):
        clock.now += 1 / 30
        decisions.append(detector(looking(0.12)))
    assert not any(decisions)
    # A blink while looking down still is one.
    clock.now += 1 / 30
    assert detector(looking(0.12, aperture=0.02))


def test_a_long_narrowing_without_closing_is_used_before_the_reference_knows_it() -> None:
    """Never having looked down (the reference cannot follow yet), a sustained look at the bottom
    is held as a possible blink for at most 0.3 s (less once the narrow frames have taught the
    reference how far the lids drop), then used and learned from."""
    clock = Ticks()
    detector = Blinks(clock=clock)
    for _ in range(100):
        clock.now += 1 / 30
        assert not detector(looking(0.0))
    decisions = []
    for _ in range(30):
        clock.now += 1 / 30
        decisions.append(detector(looking(0.12)))
    first_used = decisions.index(False)
    assert 0 < first_used / 30 <= 0.34 and not any(decisions[first_used:])
    # Eyes that are actually shut stay rejected however long they are shut.
    assert all(
        detector(looking(0.12, aperture=0.01)) for _ in range(100) if not setattr(clock, "now", clock.now + 1 / 30)
    )


def test_the_live_point_follows_the_eyes_to_the_bottom_of_the_screen() -> None:
    """End to end through the tracker: reading the upper screen, then a look at the bottom. The
    point must get there, not stop where it was (the middle) as it did with a fixed reference."""

    class LookingEstimator(Estimator):
        def __init__(self) -> None:
            self.v = 0.0
            self.blinks = Blinks()

        def extract_features(self, frame: object) -> tuple[object, bool]:
            return list(frame), self.blinks(looking(self.v))

    t, scene, receiver, *_, clock, log = tracker()
    estimator = LookingEstimator()
    t.estimator, receiver.live = estimator, True
    for k in range(300):  # the upper two thirds, drifting up and down
        estimator.v = 0.02 + 0.04 * np.sin(2 * np.pi * k / 120)
        scene.point = (960.0, 540.0 + 300 * np.sin(2 * np.pi * k / 120) - 200)
        t.step()
    estimator.v, scene.point = 0.15, (960.0, 1000.0)  # a large monitor's bottom: the gap 72% of the average
    before = len(receiver.points)
    run_for(t, clock, 0.5)
    ys = [y * 1080 for _, _, y in receiver.points[before:]]
    assert len(ys) >= 10 and ys[-1] > 950

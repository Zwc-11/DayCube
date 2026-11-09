"""What slides with the gaze during long stares: slopes from frame vectors."""

import math

import numpy as np
import pytest
from gaze.drift import DriftProbe


def vector(pitch_deg: float, distance_mm: float, iris_y: float, upper: float, lower: float) -> np.ndarray:
    a = math.radians(pitch_deg)
    R = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    eyes = np.array([[-30.0, 0, 0], [30.0, 0, 0]])
    features = np.array([[0.0, iris_y, upper, lower]] * 2)
    return np.concatenate([R.ravel(), [0.0, 0.0, distance_mm], eyes.ravel(), features.ravel()])


def stare(probe: DriftProbe, began: float, seconds: float, *, x_speed: float = 0.0, fps: float = 30.0) -> None:
    """A look held for ``seconds``: the gaze slides down 12 px/s as the upper lid comes down
    0.05 mm/s and the irises read 0.001/s lower; the head holds still."""
    for n in range(int(seconds * fps)):
        t = began + n / fps
        u = t - began
        probe.add(t, began, 800 + x_speed * u, 500 + 12 * u, vector(5.0, 600.0, 0.2 + 0.001 * u, 4 - 0.05 * u, -4.0))


def test_a_long_stare_gives_the_slopes_of_what_moved() -> None:
    probe = DriftProbe(1920)
    stare(probe, 100.0, 4.0)
    assert probe.state() is None  # still going: counted when the next look begins
    stare(probe, 104.1, 0.3)
    state = probe.state()
    assert state is not None and state["stares"] == 1
    assert state["gaze_px_s"] == pytest.approx(12.0, abs=0.1)
    assert state["lids_mm_s"] == pytest.approx(-0.05, abs=1e-3)
    assert state["iris_per_s"] == pytest.approx(0.001, abs=1e-5)
    assert state["pitch_deg_s"] == pytest.approx(0.0, abs=1e-3)
    assert state["distance_mm_s"] == pytest.approx(0.0, abs=1e-2)


def test_short_looks_and_reading_are_not_stares() -> None:
    probe = DriftProbe(1920)
    stare(probe, 100.0, 1.5)  # too short
    stare(probe, 101.6, 4.0, x_speed=120.0)  # moving across: reading a line
    stare(probe, 105.7, 0.3)
    assert probe.state() is None


def test_another_kind_of_vector_is_skipped() -> None:
    probe = DriftProbe(1920)
    for n in range(120):
        probe.add(100 + n / 30, 100.0, 800.0, 500.0, np.zeros(12))
    probe.add(110.0, 110.0, 800.0, 500.0, np.zeros(12))
    assert probe.state() is None


def test_each_stare_goes_to_the_learner_as_its_start_and_its_end() -> None:
    got = []
    probe = DriftProbe(1920, on_stare=lambda t, A, B, seconds: got.append((t, A, B, seconds)))
    stare(probe, 100.0, 4.0)
    stare(probe, 104.1, 0.3)
    assert len(got) == 1
    t, A, B, seconds = got[0]
    assert t == 100.0 and seconds == pytest.approx(4.0, abs=0.05)
    # Upper lid: 4 - 0.05 s, so about 3.97 early (0.3-0.8 s in) and 3.81 over the last half second.
    assert A[20] == pytest.approx(4 - 0.05 * 0.55, abs=0.01) and B[20] == pytest.approx(4 - 0.05 * 3.73, abs=0.01)


def test_a_very_long_look_is_cut_into_stares() -> None:
    got = []
    probe = DriftProbe(1920, on_stare=lambda *a: got.append(a))
    stare(probe, 100.0, 65.0)
    stare(probe, 165.1, 0.3)
    assert len(got) == 3 and probe.state()["stares"] == 3

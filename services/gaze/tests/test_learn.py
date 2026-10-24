"""Learning from clicks (gaze.learn) on synthetic faces: a calibration that is off, then clicks from
other head positions, some made without looking. The learner must fix the calibration's error, keep
bad clicks from making it worse, and remember what it learned for this calibration only."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")

from gaze.head import GazeModel, HeadEstimator, Poser, pack  # noqa: E402
from gaze.learn import CLICKS_FILE, LEARNED_FILE, Learner, steady  # noqa: E402
from test_head import CAMERA, GRID, HOME, MOVED, SCREEN, face  # noqa: E402

MONITOR = (1920, 1080)
CALIBRATED = "2026-10-24T15:22:00+00:00"


def estimator(offset: tuple[float, float] = (90.0, -60.0)) -> HeadEstimator:
    """A head-aware estimator calibrated while the eyes sat ``offset`` px away from each dot (a
    calibration that came out off: the person drifted, or the dots were looked at from the side)."""
    rng = np.random.default_rng(11)
    poser = Poser(CAMERA)
    heads = [poser.measure(face((x + offset[0], y + offset[1]), *HOME, rng)) for x, y in GRID for _ in range(3)]
    model = GazeModel(SCREEN).fit(heads, np.array([p for p in GRID for _ in range(3)], dtype=float))
    out = HeadEstimator(MONITOR, SCREEN.mm_per_px, landmarker=object())
    out.model = model
    return out


def clicks(n: int, seed: int, elsewhere: float = 0.0) -> list[tuple[float, float, np.ndarray, np.ndarray]]:
    """(x, y, frame vector, true point): clicks at random points from the moved head poses; a share
    ``elsewhere`` made while looking at another random point."""
    rng = np.random.default_rng(seed)
    poser = Poser(CAMERA)
    out = []
    for k in range(n):
        R, t = MOVED[k % len(MOVED)] if k % 3 else HOME
        looked = rng.uniform([60, 60], [1860, 1020])
        vector = pack(poser.measure(face(tuple(looked), R, t, rng)))
        clicked = rng.uniform([0, 0], MONITOR) if rng.random() < elsewhere else looked + rng.normal(0, 25, 2)
        out.append((float(clicked[0]), float(clicked[1]), vector, looked))
    return out


def error(model_estimator: HeadEstimator, sample: list) -> float:
    predicted = model_estimator.predict(np.array([v for *_, v, _ in sample]))
    return float(np.median(np.linalg.norm(predicted - np.array([p for *_, p in sample]), axis=1)))


def test_clicks_fix_a_calibration_that_is_off() -> None:
    est = estimator()
    learner = Learner(est, MONITOR, None, CALIBRATED)
    test = clicks(60, seed=99)
    before = error(est, test)
    assert before > 80  # the calibration's offset
    for k, (x, y, vector, _) in enumerate(clicks(200, seed=1, elsewhere=0.15)):
        learner.add(1_800_000_000.0 + 2 * k, x, y, vector)
    after = error(est, test)
    assert learner.version >= 1 and after < 0.5 * before, (before, after)
    state = learner.state()
    assert state["fit"]["error_px"]["calibration"] > state["fit"]["error_px"]["learned"]
    assert state["skipped"]["not looking"] > 0  # most of the clicks made elsewhere were left out


def test_clicks_made_without_looking_never_make_it_worse() -> None:
    est = estimator(offset=(0.0, 0.0))  # a good calibration
    learner = Learner(est, MONITOR, None, CALIBRATED)
    weights = [w.copy() for _, _, w in est.model.eyes]
    test = clicks(60, seed=99)
    before = error(est, test)
    rng = np.random.default_rng(5)
    for k, (_, _, vector, _) in enumerate(clicks(300, seed=2)):
        x, y = rng.uniform([0, 0], MONITOR)  # every click somewhere the eyes were not
        learner.add(1_800_000_000.0 + 2 * k, float(x), float(y), vector)
    assert error(est, test) <= before * 1.02
    if learner.version == 0:
        assert all(np.array_equal(w, e[2]) for w, e in zip(weights, est.model.eyes, strict=True))


def test_the_label_is_the_steady_look_before_the_click() -> None:
    """Frames: a saccade, then 200 ms on the target, then the eyes leave as the button goes down."""
    frames = []
    t = 100.0
    for k in range(6):  # moving fast across the screen
        frames.append((t, np.full(26, float(k)), 200.0 + 150 * k, 300.0))
        t += 1 / 30
    for _ in range(6):  # steady on the target
        frames.append((t, np.full(26, 9.0), 1500.0, 500.0))
        t += 1 / 30
    frames.append((t, np.full(26, 7.0), 1200.0, 700.0))  # already moving on at the click
    look = steady(frames, 1920)
    assert look is not None and [frames[i][1][0] for i in look] == [9.0] * 6
    assert steady(frames[:7], 1920) is None  # a saccade alone is no look


def test_a_click_uses_the_frames_before_it_through_the_tracker_buffer() -> None:
    est = estimator(offset=(0.0, 0.0))
    learner = Learner(est, MONITOR, None, CALIBRATED)
    poser = Poser(CAMERA)
    vector = pack(poser.measure(face((700, 400), *HOME)))
    x, y = est.predict(vector[None, :])[0]
    for k in range(10):
        learner.frame(50.0 + k / 30, vector, float(x), float(y))
    assert learner.click(50.0 + 10 / 30, 700.0, 400.0) == "used"
    assert learner.click(50.0 + 10 / 30, 2500.0, 400.0) == "off the monitor"
    assert learner.click(60.0, 700.0, 400.0) == "no steady look"  # nothing seen in the half second before
    assert learner.clicks[0].error_px < 40


def test_what_is_learned_is_kept_for_this_calibration_only(tmp_path: Path) -> None:
    est = estimator()
    learner = Learner(est, MONITOR, tmp_path, CALIBRATED)
    for k, (x, y, vector, _) in enumerate(clicks(120, seed=3)):
        learner.add(1_800_000_000.0 + 2 * k, x, y, vector)
    assert learner.version >= 1
    assert len((tmp_path / CLICKS_FILE).read_text().splitlines()) == 120
    saved = json.loads((tmp_path / LEARNED_FILE).read_text())
    assert saved["calibration"] == CALIBRATED and saved["version"] == learner.version
    test = clicks(30, seed=98)

    again = estimator()
    restored = Learner(again, MONITOR, tmp_path, CALIBRATED)
    assert restored.version == learner.version and len(restored.clicks) == 120
    assert error(again, test) == pytest.approx(error(est, test))

    fresh = estimator()
    recalibrated = Learner(fresh, MONITOR, tmp_path, "2030-01-01T00:00:00+00:00")
    assert recalibrated.version == 0 and recalibrated.clicks == []  # clicks from before it are not used
    assert error(fresh, test) > error(est, test)


def test_in_the_tracker_a_fit_runs_beside_the_frames() -> None:
    est = estimator()
    learner = Learner(est, MONITOR, None, CALIBRATED, background=True)
    test = clicks(40, seed=99)
    before = error(est, test)
    for k, (x, y, vector, _) in enumerate(clicks(160, seed=4)):
        learner.add(1_800_000_000.0 + 2 * k, x, y, vector)
        if learner._worker is not None:
            learner._worker.join()  # a test waits; the tracker does not
    assert learner.version >= 1 and error(est, test) < 0.5 * before


def test_head_terms_learn_an_error_that_follows_where_the_head_is() -> None:
    """A webcam whose field of view is not the assumed one puts the head in the wrong place, so the
    gaze error grows as the head moves from where it was at calibration. Eye terms alone cannot
    learn that; the head's place and turn as terms can."""
    from gaze.head import Camera

    wrong = Camera.nominal(1280, 720, hfov_deg=50.0)  # the real webcam is 65 degrees
    rng = np.random.default_rng(21)

    def measured(target: tuple[float, float], R: np.ndarray, t: np.ndarray) -> np.ndarray:
        return pack(Poser(wrong).measure(face(target, R, t, rng)))

    def calibrated() -> HeadEstimator:
        heads = [Poser(wrong).measure(face(p, *HOME, rng)) for p in GRID for _ in range(3)]
        out = HeadEstimator(MONITOR, SCREEN.mm_per_px, landmarker=object())
        out.model = GazeModel(SCREEN).fit(heads, np.array([p for p in GRID for _ in range(3)], dtype=float))
        return out

    poses = [(R, t + np.array([dx, dy, dz])) for R, t in [HOME, *MOVED] for dx, dy, dz in
             [(0, 0, 0), (60, 0, 0), (-60, 20, 0), (0, 0, 120), (40, -30, -80)]]  # fmt: skip
    stream, test = [], []
    for k in range(260):
        R, t = poses[k % len(poses)]
        looked = rng.uniform([60, 60], [1860, 1020])
        stream.append((float(looked[0]), float(looked[1]), measured(tuple(looked), R, t)))
    for k in range(60):
        R, t = poses[(7 * k + 3) % len(poses)]
        looked = rng.uniform([60, 60], [1860, 1020])
        test.append((float(looked[0]), float(looked[1]), measured(tuple(looked), R, t), looked))

    results = {}
    for head_terms in (False, True):
        est = calibrated()
        learner = Learner(est, MONITOR, None, CALIBRATED, head_terms=head_terms)
        for k, (x, y, vector) in enumerate(stream):
            learner.add(1_800_000_000.0 + 2 * k, x, y, vector)
        results[head_terms] = error(est, test)
    before = error(calibrated(), test)
    assert results[False] < before and results[True] < 0.8 * results[False], (before, results)

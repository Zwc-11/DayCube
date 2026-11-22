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
from gaze.learn import (  # noqa: E402
    CLICKS_FILE,
    HISTORY_FILE,
    LEARNED_FILE,
    LINES_FILE,
    TUNED_FILE,
    Learner,
    Settings,
    steady,
)
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


def test_live_accuracy_is_measured_on_clicks_no_model_had_seen(tmp_path: Path) -> None:
    """Each click is predicted before anything learns from it, by the model in use and by the
    calibration alone: after an update, the clicks that follow show the learned model beating the
    calibration on the same clicks, which neither had learned from."""
    est = estimator()
    learner = Learner(est, MONITOR, tmp_path, CALIBRATED)
    for k, (x, y, vector, _) in enumerate(clicks(200, seed=5)):
        learner.add(1_800_000_000.0 + 2 * k, x, y, vector)
    assert learner.version >= 1
    live = learner.state()["live"]
    assert live["clicks"] == 200
    first, last = live["by_version"][0], live["by_version"][-1]
    assert first["version"] == 0 and first["error_px"] == first["calibration_px"]  # the same model
    assert 1 <= last["version"] <= learner.version and last["error_px"] < 0.6 * last["calibration_px"], live
    assert live["ratio"] < 0.6
    rows = [json.loads(line) for line in (tmp_path / CLICKS_FILE).read_text().splitlines()]
    assert {r["v"] for r in rows} == {v["version"] for v in live["by_version"]}
    assert all(r["used"] in (True, False) and r["base_px"] is not None for r in rows)
    history = [json.loads(line) for line in (tmp_path / HISTORY_FILE).read_text().splitlines()]
    fits = [h for h in history if h["event"] == "fit"]
    assert sum(h["accepted"] for h in fits) == learner.version
    assert all({"held_out", "error_px", "version", "head_terms", "t"} <= set(h) for h in fits)


def test_the_tracker_records_the_first_frame_an_update_reaches(tmp_path: Path) -> None:
    """An update is swapped into the model the tracker predicts with: the very next frame uses
    the new weights, and the tracker logs that frame as the update applied."""
    from gaze.track import Tracker
    from test_track import CALIBRATION, Camera, Clock, Computer, Receiver, Scene

    est = estimator()
    learner = Learner(est, MONITOR, tmp_path, CALIBRATED)
    vector = pack(Poser(CAMERA).measure(face((700, 400), *HOME)))
    seen: list[np.ndarray] = []

    class Head:
        model = est.model

        def extract_features(self, frame: object) -> tuple[np.ndarray, bool]:
            return vector, False

        def predict(self, X: np.ndarray) -> np.ndarray:
            seen.append(out := est.predict(X))
            return out

    clock, scene, log = Clock(), Scene(), []
    tracker = Tracker(Head(), lambda: Camera(scene, log), Receiver(), CALIBRATION | {"monitor": list(MONITOR)},
                      system=Computer(), clock=clock, sleep=clock.sleep, timer=clock, learner=learner)  # fmt: skip
    while not seen:
        tracker.step()
    for k, (x, y, v, _) in enumerate(clicks(120, seed=3)):
        learner.add(1_800_000_000.0 + 2 * k, x, y, v)
    assert learner.version >= 1 and learner.applied is None
    frames = len(seen)
    tracker.step()
    assert len(seen) == frames + 1 and not np.allclose(seen[-1], seen[-2])  # the next frame: new weights
    assert learner.applied is not None and learner.applied["version"] == learner.version
    assert learner.applied["after_ms"] is not None
    assert tracker.state()["learning"]["applied"]["version"] == learner.version
    events = [json.loads(line)["event"] for line in (tmp_path / HISTORY_FILE).read_text().splitlines()]
    assert events[-1] == "applied" and "fit" in events


def test_an_update_loaded_at_start_is_recorded_as_applied_on_the_first_frame(tmp_path: Path) -> None:
    """The tracker restarted: the learner loads update N before the tracker exists, and the first
    frame predicts with it. That frame records it applied (doctor waited for the next fit)."""
    from gaze.track import Tracker
    from test_track import CALIBRATION, Camera, Clock, Computer, Receiver, Scene

    first = Learner(estimator(), MONITOR, tmp_path, CALIBRATED)
    for k, (x, y, v, _) in enumerate(clicks(120, seed=3)):
        first.add(1_800_000_000.0 + 2 * k, x, y, v)
    assert first.version >= 1
    est = estimator()
    learner = Learner(est, MONITOR, tmp_path, CALIBRATED)  # after the restart
    assert learner.version == first.version and learner.applied is None
    vector = pack(Poser(CAMERA).measure(face((700, 400), *HOME)))

    class Head:
        model = est.model

        def extract_features(self, frame: object) -> tuple[np.ndarray, bool]:
            return vector, False

        def predict(self, X: np.ndarray) -> np.ndarray:
            return est.predict(X)

    clock, scene, log = Clock(), Scene(), []
    tracker = Tracker(Head(), lambda: Camera(scene, log), Receiver(), CALIBRATION | {"monitor": list(MONITOR)},
                      system=Computer(), clock=clock, sleep=clock.sleep, timer=clock, learner=learner)  # fmt: skip
    for _ in range(200):
        tracker.step()
        if learner.applied:
            break
    assert learner.applied is not None and learner.applied["version"] == first.version
    assert learner.applied["after_ms"] is None  # fitted before this tracker started


def lines(n: int, seed: int, shift: float = 0.0) -> list[tuple[float, float, float, np.ndarray, np.ndarray]]:
    """(y, x0, x1, frame vector, true point): looks along lines being read, from the moved head
    poses; the label's height ``shift`` px off where the eyes were (a decoder a line astray)."""
    rng = np.random.default_rng(seed)
    poser = Poser(CAMERA)
    out = []
    for k in range(n):
        R, t = MOVED[k % len(MOVED)] if k % 3 else HOME
        looked = rng.uniform([360, 60], [1560, 1020])
        vector = pack(poser.measure(face(tuple(looked), R, t, rng)))
        x0 = looked[0] - rng.uniform(50, 550)
        out.append((float(looked[1] + shift), float(x0), float(x0 + 600), vector, looked))
    return out


def test_reading_lines_teach_what_clicks_alone_learn_slowly() -> None:
    """A calibration off vertically; few clicks, many lines read: the lines carry the vertical."""
    test = clicks(60, seed=99)
    results = {}
    for use_lines in (False, True):
        est = estimator(offset=(0.0, -90.0))
        learner = Learner(est, MONITOR, None, CALIBRATED)
        stream = sorted(
            [(1_800_000_000.0 + 30 * k, "click", c) for k, c in enumerate(clicks(40, seed=7))]
            + [(1_800_000_000.0 + 3 * k + 1, "line", ln) for k, ln in enumerate(lines(400, seed=8))],
            key=lambda e: e[0],
        )
        for t, kind, item in stream:
            if kind == "click":
                learner.add(t, item[0], item[1], item[2])
            elif use_lines:
                learner.add_line(t, item[0], item[1], item[2], item[3])
        predicted = est.predict(np.array([v for *_, v, _ in test]))
        results[use_lines] = float(np.median(np.abs(predicted[:, 1] - np.array([p[1] for *_, p in test]))))
        if use_lines:
            assert learner.line_counts["used"] > 300 and learner.fit["lines"] > 0
    assert results[True] < 0.75 * results[False], results  # the vertical, which lines label


def test_lines_a_decoder_got_wrong_cannot_make_tracking_worse() -> None:
    """Every line 150 px off (a decoder astray): clicks judge every fit, so none of it is taken
    unless the clicks say the model is better."""
    est = estimator(offset=(0.0, 0.0))
    learner = Learner(est, MONITOR, None, CALIBRATED, settings=Settings(line_weight=1.0))
    test = clicks(60, seed=99)
    before = error(est, test)
    stream = sorted(
        [(1_800_000_000.0 + 20 * k, "click", c) for k, c in enumerate(clicks(120, seed=9))]
        + [(1_800_000_000.0 + 2 * k + 1, "line", ln) for k, ln in enumerate(lines(1200, seed=10, shift=150.0))],
        key=lambda e: e[0],
    )
    for t, kind, item in stream:
        if kind == "click":
            learner.add(t, item[0], item[1], item[2])
        else:
            learner.add_line(t, item[0], item[1], item[2], item[3])
    assert error(est, test) <= before * 1.05, (before, error(est, test))


def test_a_line_label_finds_the_fixation_it_names(tmp_path: Path) -> None:
    est = estimator(offset=(0.0, 0.0))
    learner = Learner(est, MONITOR, tmp_path, CALIBRATED)
    vector = pack(Poser(CAMERA).measure(face((700, 400), *HOME)))
    x, y = est.predict(vector[None, :])[0]
    t0 = 1_800_000_000.0
    for k in range(9):
        learner.frame(t0 + k / 30, vector, float(x), float(y))
    learner.fixation("f1", t0, t0 + 8 / 30)
    learner.fixation("f2", t0 + 0.2, t0 + 0.25)  # one frame: too short to keep
    assert learner.line("f1", 400.0, 500.0, 1100.0) == "used"
    assert learner.line("f1", 400.0, 500.0, 1100.0) == "fixation not found"  # each look labels once
    assert learner.line("f2", 400.0, 500.0, 1100.0) == "fixation not found"
    assert learner.line("f3", 400.0, 500.0, 1100.0) == "fixation not found"
    kept = learner.lines[0]
    assert kept.kind == "line" and kept.span == (500.0, 1100.0) and 500 <= kept.x <= 1100 and kept.y == 400.0
    saved = [json.loads(line) for line in (tmp_path / LINES_FILE).read_text().splitlines()]
    assert saved[0]["span"] == [500.0, 1100.0]
    again = Learner(estimator(offset=(0.0, 0.0)), MONITOR, tmp_path, CALIBRATED)
    assert len(again.lines) == 1 and again.lines[0].span == (500.0, 1100.0)
    assert learner.state()["lines"] == {"used": 1, "off the line": 0, "fixation not found": 3, "kept": 1}


def test_settings_are_held_to_their_bounds_and_tuned_ones_are_read_before_a_fit(tmp_path: Path) -> None:
    settings = Settings.from_json({"gate_k": 99, "refit_every": 7.6, "line_weight": "x", "unknown": 1})
    assert settings.gate_k == 6.0 and settings.refit_every == 8 and settings.line_weight == Settings().line_weight
    assert Settings.from_json(None) == Settings()
    learner = Learner(estimator(), MONITOR, tmp_path, CALIBRATED)
    assert learner.settings == Settings()
    (tmp_path / TUNED_FILE).write_text(json.dumps({"settings": {"refit_every": 40, "gate_k": 2.0}}))
    learner.refit()  # too few clicks to fit, but the settings are read first
    assert learner.settings.refit_every == 40 and learner.settings.gate_k == 2.0
    assert learner.state()["tuned"] is True
    fixed = Learner(estimator(), MONITOR, tmp_path, CALIBRATED, settings=Settings(refit_every=10))
    fixed.refit()
    assert fixed.settings.refit_every == 10  # settings given are kept


def drooped(vector: np.ndarray, mm: float) -> np.ndarray:
    out = np.array(vector, dtype=float)
    out[[20, 24]] += mm  # each eye's upper lid (head frame y points down)
    return out


def droop_px(model_estimator: HeadEstimator, sample: list, mm: float = 1.0) -> float:
    X = np.array([v for *_, v, _ in sample])
    moved = model_estimator.predict(np.array([drooped(v, mm) for v in X])) - model_estimator.predict(X)
    return float(np.median(moved[:, 1]))


def test_stares_teach_that_a_drooping_lid_is_not_a_look_down(tmp_path: Path) -> None:
    """The calibrated model reads the vertical partly from the lids, so a lid drooping over a long
    stare moves the gaze down. Stares (the eyes did not move; the lids came down 0.3-0.9 mm) teach
    the fit to stop that, and the clicks' error does not suffer."""
    test = clicks(60, seed=99)
    results = {}
    for weight in (0.0, 1.0):
        est = estimator(offset=(40.0, -30.0))
        learner = Learner(est, MONITOR, tmp_path if weight else None, CALIBRATED,
                          settings=Settings(stare_weight=weight))  # fmt: skip
        before = droop_px(est, test)
        rng = np.random.default_rng(3)
        for k, (x, y, vector, _) in enumerate(clicks(200, seed=1)):
            t = 1_800_000_000.0 + 2 * k
            if k % 4 == 0:
                assert learner.add_stare(t - 1, vector, drooped(vector, rng.uniform(0.3, 0.9)), 4.0) == "used"
            learner.add(t, x, y, vector)
        results[weight] = (droop_px(est, test), error(est, test), learner.version)
    assert before > 10  # the calibration: 1 mm of droop moves the gaze down
    (droop_plain, error_plain, v_plain), (droop_stares, error_stares, v_stares) = results[0.0], results[1.0]
    assert v_plain >= 1 and v_stares >= 1
    assert abs(droop_stares) < 0.5 * abs(droop_plain), results
    assert error_stares < 1.1 * error_plain, results
    state = learner.state()
    assert state["stares"]["used"] == 50 and state["fit"]["stares"] > 0
    assert len((tmp_path / "stares.jsonl").read_text().splitlines()) == 50
    again = Learner(estimator(offset=(40.0, -30.0)), MONITOR, tmp_path, CALIBRATED)
    assert len(again.stares) == 50  # kept for this calibration


def test_a_stare_whose_ends_the_model_puts_far_apart_is_not_used() -> None:
    est = estimator(offset=(0.0, 0.0))
    learner = Learner(est, MONITOR, None, CALIBRATED)
    rng = np.random.default_rng(8)
    poser = Poser(CAMERA)
    a, b = (pack(poser.measure(face(point, *HOME, rng))) for point in ((300.0, 300.0), (1500.0, 800.0)))
    learner.add_stare(1_800_000_000.0, a, a)
    assert learner.add_stare(1_800_000_010.0, a, b) == "moved"  # the eyes moved: not a stare
    assert len(learner.stares) == 1 and learner.state()["stares"] == {"used": 1, "moved": 1, "kept": 1}

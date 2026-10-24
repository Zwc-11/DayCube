"""Tuning learning to one person (gaze.tune): the replay is learning as it ran live, the search
never sees the clicks that judge it, and the tracker's learner reads what it chose."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")

from gaze import tune  # noqa: E402
from gaze.learn import CLICKS_FILE, HISTORY_FILE, LINES_FILE, TUNED_FILE, Learner, Settings  # noqa: E402
from test_learn import CALIBRATED, MONITOR, clicks, estimator, lines  # noqa: E402

T0 = 1_800_000_000.0


def vault(tmp_path: Path, n: int, offset: tuple[float, float] = (90.0, -60.0), with_lines: int = 0) -> Path:
    """A vault with a calibration that is off and ``n`` clicks (and lines) learned from live."""
    folder = tmp_path / "calibration"
    folder.mkdir(parents=True)
    est = estimator(offset)
    (folder / "gaze-head.json").write_text(json.dumps({"kind": "head", **est.model.to_json()}))
    (folder / "gaze.json").write_text(json.dumps({"t": CALIBRATED, "monitor": list(MONITOR), "model": "head",
                                                  "model_file": "gaze-head.json"}))  # fmt: skip
    learner = Learner(est, MONITOR, folder, CALIBRATED, settings=Settings())
    for k, (x, y, vector, _) in enumerate(clicks(n, seed=3, elsewhere=0.1)):
        learner.add(T0 + 10 * k, x, y, vector)
    for k, (y, x0, x1, vector, _) in enumerate(lines(with_lines, seed=4)):
        learner.add_line(T0 + 10 * k + 5, y, x0, x1, vector)
    return tmp_path


def test_a_replay_is_learning_as_it_ran_live(tmp_path: Path) -> None:
    root = vault(tmp_path, 160)
    folder = root / "calibration"
    live = [json.loads(line)["error_px"] for line in (folder / CLICKS_FILE).read_text().splitlines()]
    model_json = json.loads((folder / "gaze-head.json").read_text())
    recorded = [learner_click for learner_click in tune.load_labels(folder / CLICKS_FILE, None, (26,), "click", 3000)]
    again = tune.replay(model_json, MONITOR, recorded, [], Settings())
    np.testing.assert_allclose(again, live, atol=0.11)  # the file keeps a tenth of a pixel


def test_the_search_is_judged_on_clicks_it_never_saw(tmp_path: Path) -> None:
    root = vault(tmp_path, 320, with_lines=200)
    folder = root / "calibration"
    (folder / TUNED_FILE).write_text(json.dumps({"settings": {"refit_every": 100}, "t": "2020-01-01T00:00:00+00:00"}))
    seen: list[np.ndarray] = []
    real_search = tune.search

    def spy(run, start, judge, grid, passes=tune.PASSES):  # noqa: ANN001
        seen.append(judge.copy())
        return real_search(run, start, judge, grid, passes)

    tune.search = spy
    try:
        result = tune.tune(root, say=lambda _: None, grid={"refit_every": (10, 100)})
    finally:
        tune.search = real_search
    assert result["tuned"] and result["clicks"] == 320 and result["lines"] == 200
    judge = seen[0]
    held = int(320 - tune.SEARCH_SHARE * 320)
    assert not judge[-held:].any() and judge[tune.WARMUP : 320 - held].all()  # the newest 30% stay unseen
    held_out = result["replay"]["held_out_px"]
    written = json.loads((folder / TUNED_FILE).read_text())
    if result["accepted"]:
        assert held_out["best"] <= tune.MARGIN * held_out["in_use"]
        assert written["settings"]["refit_every"] == 10
    else:
        assert written["settings"]["refit_every"] == 100
    event = json.loads((folder / HISTORY_FILE).read_text().splitlines()[-1])
    assert event["event"] == "tuned" and event["accepted"] == result["accepted"]
    learner = Learner(estimator(), MONITOR, folder, CALIBRATED)
    assert learner.settings == Settings.from_json(written["settings"])  # what the tracker learns with next


def test_tuning_waits_for_enough_new_clicks_and_a_day(tmp_path: Path) -> None:
    folder = tmp_path / "calibration"
    folder.mkdir()
    few = [type("C", (), {"t": T0 + k})() for k in range(tune.MIN_CLICKS - 1)]
    assert not tune.due(folder, few, T0)
    many = [type("C", (), {"t": T0 + k})() for k in range(tune.MIN_CLICKS + 250)]
    assert tune.due(folder, many, T0)  # never tuned
    (folder / TUNED_FILE).write_text(json.dumps({"t": "2027-01-15T08:10:00+00:00"}))  # after all of them
    assert not tune.due(folder, many, T0 + 3600)  # within a day
    assert not tune.due(folder, many, T0 + 2 * 86400)  # a day on, but no clicks since
    later = many + [type("C", (), {"t": T0 + 2 * 86400 + k})() for k in range(tune.NEW_CLICKS)]
    assert tune.due(folder, later, T0 + 2 * 86400 + 600)


def test_too_few_clicks_is_said_not_guessed(tmp_path: Path) -> None:
    root = vault(tmp_path, 40)
    result = tune.tune(root, say=lambda _: None)
    assert result == {"tuned": False, "reason": f"40 clicks since calibrating; tuning needs {tune.MIN_CLICKS}"}
    assert not (root / "calibration" / TUNED_FILE).exists()
    assert (root / "calibration" / LINES_FILE).exists() is False

"""Does tuning learning to one person's own clicks (``gaze.tune``) make their tracking better on
clicks it has not seen?

MPIIFaceGaze, as ``eval/click_learning.py`` replays it: per person, calibrated on 13 images of the
first day, then the first ``CLICKS`` images of the following days as clicks (20 mm label noise,
15% made while looking elsewhere). ``gaze.tune`` then runs exactly as on the PC: it sees only the
clicks (their labels, never where the eyes really were), searches the settings on the older 70%
and takes the best only if it is 3% better on the newest 30% by the clicks' own labels.

Reported on those newest 30%, against where the eyes really were (which tuning never saw): the
median error with the default settings and with what tuning chose, per person and overall.

    services/gaze/.venv/bin/python eval/tune_replay.py ~/data/mpiifacegaze/cache --out eval/results/tune-replay.json
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "eval"), str(ROOT / "services/gaze")]

CLICKS = 1000  # a week or two of clicks


def person(path: Path) -> dict:
    import click_learning as cl
    from gaze import tune
    from gaze.face import Face
    from gaze.head import Camera, GazeModel, Poser, Screen, pack
    from gaze.learn import Click, Learner, Settings

    d = dict(np.load(path))
    size = tuple(int(v) for v in d["image_size"])
    found = np.isfinite(d["points"][:, 0, 0])
    days, target = d["days"], d["target_px"].astype(float)
    counts = {day: int(((days == day) & found).sum()) for day in np.unique(days)}
    cal_day = next(day for day in sorted(counts) if counts[day] >= cl.CALIBRATION + 10)
    cal = np.flatnonzero((days == cal_day) & found)[: cl.CALIBRATION]
    later = np.flatnonzero((days > cal_day) & found)[:CLICKS]
    mm_per_px = float(d["screen_mm"][0] / d["screen_px"][0])
    monitor = (int(d["screen_px"][0]), int(d["screen_px"][1]))
    poser = Poser(Camera.nominal(*size))
    heads = {int(i): poser.measure(Face(d["points"][i].astype(float), size)) for i in list(cal) + list(later)}
    model = GazeModel(Screen.below_camera(monitor, float(d["screen_mm"][0]))).fit([heads[i] for i in cal], target[cal])
    model_json = model.to_json()
    rng = np.random.default_rng(int(path.stem[1:]))
    sigma = cl.LABEL_MM / mm_per_px / np.sqrt(np.pi / 2)
    clicks, truth = [], []
    for n, i in enumerate(later):
        if rng.random() < 0.15:
            label = rng.uniform([0, 0], monitor)
        else:
            label = np.clip(target[i] + rng.normal(0, sigma, 2), [0, 0], np.array(monitor) - 1)
        clicks.append(
            Click(float(days[i]) * 86400 + n * 30.0, float(label[0]), float(label[1]), pack(heads[int(i)]), 0.0)
        )
        truth.append(target[i])
    truth = np.array(truth)

    def run(s: Settings, against_truth: bool = False) -> np.ndarray:
        estimator = tune._Estimator(GazeModel.from_json(model_json))
        learner = Learner(estimator, monitor, None, None, settings=s)
        errors = np.empty(len(clicks))
        for k, c in enumerate(clicks):
            if against_truth:
                p = estimator.predict(c.X[None, :])[0]
                errors[k] = float(np.hypot(*(p - truth[k]))) * mm_per_px
            learner.add(c.t, c.x, c.y, c.X)
            if not against_truth:
                errors[k] = learner.clicks[-1].error_px
        return errors

    cut = int(tune.SEARCH_SHARE * len(clicks))
    index = np.arange(len(clicks))
    seen, held = (index >= tune.WARMUP) & (index < cut), index >= cut
    grid = {k: v for k, v in tune.SEARCH.items() if k != "line_weight"}
    best, _, trials = tune.search(run, Settings(), seen, grid)
    labels_default, labels_best = run(Settings())[held], run(best)[held]
    taken = best != Settings() and np.median(labels_best) <= tune.MARGIN * np.median(labels_default)
    chosen = best if taken else Settings()
    default_mm = float(np.median(run(Settings(), True)[held]))
    chosen_mm = float(np.median(run(chosen, True)[held])) if taken else default_mm
    changed = {k: [v, chosen.to_json()[k]] for k, v in Settings().to_json().items() if chosen.to_json()[k] != v}
    return {"person": path.stem, "default_mm": round(default_mm, 1), "tuned_mm": round(chosen_mm, 1),
            "taken": bool(taken), "changed": changed, "trials": len(trials)}  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cache", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    paths = sorted(args.cache.glob("p*.npz"))
    with ProcessPoolExecutor(args.workers) as pool:
        people = list(pool.map(person, paths))
    for p in people:
        print(json.dumps(p), flush=True)
    default = np.array([p["default_mm"] for p in people])
    tuned = np.array([p["tuned_mm"] for p in people])
    summary = {
        "clicks_per_person": CLICKS, "median_of_people_mm": {"default": round(float(np.median(default)), 1),
                                                             "tuned": round(float(np.median(tuned)), 1)},
        "taken_for": sum(p["taken"] for p in people), "better": int((tuned < default).sum()),
        "worse": int((tuned > default).sum()), "people": people,
    }  # fmt: skip
    print(json.dumps({k: v for k, v in summary.items() if k != "people"}))
    if args.out:
        args.out.write_text(json.dumps(summary, indent=1) + "\n")


if __name__ == "__main__":
    main()

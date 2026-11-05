"""Stares as labels: does learning that the gaze must not move during a stare stop a drooping lid
from moving it, without costing accuracy?

The head-aware model reads the vertical largely from the lids (the upper lid follows the eye down),
so a lid that droops while the eyes rest on one point moves the gaze down: on MPIIFaceGaze the
calibrated model puts the gaze 20 mm lower on the screen for every 0.5 mm the upper lid comes down
(15 people, median). ``gaze/drift.py`` collects long stares in ordinary use (the ring filter holding
one fixation for 2 s or more, not moving across) and the learner (``Stare``) adds to each fit that
the gaze at a stare's end must be where it was at its start.

MPIIFaceGaze has single images, not stares, so stares are made from its real images: a stare's
start is a later image's own measurements, its end the same with the upper lids 0.2-1.0 mm lower
(and measurement noise on both). Replayed as ``eval/click_learning.py`` does (15 people, their own
laptops, calibrated on 13 images of the first day, every later image predicted before anything
learns from it): 15% of the images are clicks (20 mm label noise, 15% made while looking elsewhere),
and one image in 20 also gives a stare. Compared:

- error: every later image's error against where the eyes were (after the first 100 clicks);
- droop: how far the final model moves the gaze for 0.5 mm of upper-lid droop, on each person's
  last 200 images (mm on the screen, positive: down);
- with a share of the "stares" not stares at all: the eyes moved 50-150 px between start and end.

Result (eval/results/stare-learning.json): learning from clicks alone makes the model lean on the
lids more (0.5 mm of droop: 19.8 mm down with the calibration, 23.5 mm after learning). Stares take
that back and more, at a price in accuracy: at weight 0.3 the droop is back to 19.8 mm for 3% more
median error (32.4 to 33.4 mm), at weight 1 15.2 mm for 4%, at weight 3 10.9 mm for 10%. The lids
carry the vertical on a webcam, and a stare's droop moves them the way a look down does, so
nothing separates the two for free. Stares are therefore off by default (``Settings.stare_weight``
0) and offered to tuning (``gaze/tune.py``), which takes a weight only if it is 3% better on your own
newest clicks; the stares are collected (``stares.jsonl``) either way.

    services/gaze/.venv/bin/python eval/stare_learning.py ~/data/mpiifacegaze/cache \\
        --out eval/results/stare-learning.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "eval"), str(ROOT / "services/gaze")]

CLICK_SHARE = 0.15
STARE_SHARE = 0.05
DROOP_MM = (0.2, 1.0)
UPPER = 2  # index of the upper lid's height among each eye's measurements
NOISE = (0.002, 0.002, 0.03, 0.03)  # per measurement: a stare end's mean over ~15 frames
TEST_IMAGES = 200


def droop(vector: np.ndarray, mm: float) -> np.ndarray:
    out = vector.copy()
    for eye in range(2):
        out[18 + 4 * eye + UPPER] += mm  # head frame y points down: a drooping upper lid comes down
    return out


def noisy(rng: np.random.Generator, vector: np.ndarray) -> np.ndarray:
    out = vector.copy()
    out[18:] += rng.normal(0, np.tile(NOISE, 2))
    return out


def person(path: Path, weight: float, moved: float, seed: int) -> dict:
    import click_learning as cl
    from gaze.face import Face
    from gaze.head import Camera, GazeModel, Poser, Screen, pack
    from gaze.learn import Learner, Settings

    d = dict(np.load(path))
    size = tuple(int(v) for v in d["image_size"])
    found = np.isfinite(d["points"][:, 0, 0])
    days, target = d["days"], d["target_px"].astype(float)
    counts = {day: int(((days == day) & found).sum()) for day in np.unique(days)}
    cal_day = next(day for day in sorted(counts) if counts[day] >= cl.CALIBRATION + 10)
    cal = np.flatnonzero((days == cal_day) & found)[: cl.CALIBRATION]
    later = np.flatnonzero((days > cal_day) & found)
    replayed, test = later[:-TEST_IMAGES], later[-TEST_IMAGES:]
    mm_per_px = float(d["screen_mm"][0] / d["screen_px"][0])
    monitor = (int(d["screen_px"][0]), int(d["screen_px"][1]))
    poser = Poser(Camera.nominal(*size))
    vectors = {int(i): pack(poser.measure(Face(d["points"][i].astype(float), size))) for i in np.flatnonzero(found)}
    from gaze.head import unpack

    model = GazeModel(Screen.below_camera(monitor, float(d["screen_mm"][0]))).fit(
        unpack(np.array([vectors[int(i)] for i in cal])), target[cal]
    )
    learner = Learner(
        cl.Estimator(model, monitor), monitor, None, None, settings=replace(Settings(), stare_weight=weight)
    )
    rng = np.random.default_rng(seed)
    sigma = cl.LABEL_MM / mm_per_px / np.sqrt(np.pi / 2)
    errors, stares = [], 0
    for n, i in enumerate(replayed):
        vector, truth = vectors[int(i)], target[i]
        p = learner.estimator.predict(vector[None, :])[0]
        errors.append(float(np.hypot(*(p - truth))) * mm_per_px)
        t = float(days[i]) * 86400 + n * 5.0
        if rng.random() < STARE_SHARE and weight > 0:
            start = noisy(rng, vector)
            if rng.random() < moved:  # not a stare: the eyes went to another point 50-150 px away
                near = np.hypot(*(target[replayed] - truth).T)
                others = replayed[(near >= 50) & (near <= 150)]
                end = noisy(rng, vectors[int(rng.choice(others))]) if len(others) else noisy(rng, vector)
            else:
                end = noisy(rng, droop(vector, rng.uniform(*DROOP_MM)))
            stares += learner.add_stare(t - 4.0, start, end, 4.0) == "used"
        if rng.random() < CLICK_SHARE:
            if rng.random() < 0.15:
                label = rng.uniform([0, 0], monitor)
            else:
                label = np.clip(truth + rng.normal(0, sigma, 2), [0, 0], np.array(monitor) - 1)
            learner.add(t, float(label[0]), float(label[1]), vector)
    e = np.array(errors)
    after = slice(int(cl.WARMUP / CLICK_SHARE), None)
    X = np.array([vectors[int(i)] for i in test])

    def moved_mm(estimator: object) -> float:
        a, b = estimator.predict(X), estimator.predict(np.array([droop(v, 0.5) for v in X]))
        return float(np.nanmedian(b[:, 1] - a[:, 1])) * mm_per_px

    calibration = cl.Estimator(GazeModel(model.screen, eyes=learner.prior), monitor)
    final_error = np.hypot(*(learner.estimator.predict(X) - target[test]).T) * mm_per_px
    return {
        "person": path.stem, "error_mm": round(float(np.median(e[after])), 1),
        "final_error_mm": round(float(np.nanmedian(final_error)), 1), "updates": learner.version,
        "stares_used": stares, "droop_mm": round(moved_mm(learner.estimator), 1),
        "droop_calibration_mm": round(moved_mm(calibration), 1), "raw": e[after],
    }  # fmt: skip


RUNS = (
    ("clicks alone", 0.0, 0.0),
    ("clicks and stares, weight 0.3", 0.3, 0.0),
    ("clicks and stares, weight 1", 1.0, 0.0),
    ("clicks and stares, weight 3", 3.0, 0.0),
    ("clicks and stares, weight 1, 20% of them the eyes moving", 1.0, 0.2),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cache", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--runs", nargs="*", default=[])
    args = parser.parse_args()
    paths = sorted(args.cache.glob("p*.npz"))
    out: dict = {"click_share": CLICK_SHARE, "stare_share": STARE_SHARE, "droop_mm": DROOP_MM, "runs": {}}
    for name, weight, moved in RUNS:
        if args.runs and not any(name.startswith(r) for r in args.runs):
            continue
        people = [person(p, weight, moved, seed=k) for k, p in enumerate(paths)]
        everyone = np.concatenate([r.pop("raw") for r in people])
        out["runs"][name] = {
            "median_mm": round(float(np.median(everyone)), 1),
            "p90_mm": round(float(np.percentile(everyone, 90)), 1),
            "final_error_mm": round(float(np.median([r["final_error_mm"] for r in people])), 1),
            "droop_mm": round(float(np.median([r["droop_mm"] for r in people])), 1),
            "droop_calibration_mm": round(float(np.median([r["droop_calibration_mm"] for r in people])), 1),
            "people": people,
        }
        summary = {k: v for k, v in out["runs"][name].items() if k != "people"}
        print(name, json.dumps(summary), flush=True)
    if "clicks alone" in out["runs"]:
        base = {r["person"]: r["error_mm"] for r in out["runs"]["clicks alone"]["people"]}
        for run in out["runs"].values():
            run["people_worse_by_2pct"] = [
                r["person"] for r in run["people"] if r["error_mm"] > 1.02 * base[r["person"]]
            ]
    if args.out:
        args.out.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()

"""Does learning from clicks (services/gaze/gaze/learn.py) make the gaze model more accurate over
the following days, and can bad clicks make it worse?

Real webcam images: MPIIFaceGaze, 15 people on their own laptops over days to weeks, each image
taken while they looked at a known point on the screen (eval/head_bench_extract.py caches the face
landmarks). For each person:

1. Calibrate as DayCube does on a desk: the head-aware model with a nominal webcam and the screen
   assumed centred under it (``head-assumed`` in eval/head_bench.py), from 13 images of the first
   day with enough images.
2. Replay every image of the other days in order as a click: the point the person looked at, with
   label noise (a click is not exactly where the eyes were: PACE measured 74 px, about 20 mm on a
   24" monitor, after its filtering; here a 2-D Gaussian with that mean distance), and a share of
   clicks made while looking elsewhere (a random point on the screen).
3. Prequentially: each image is first predicted, its error measured against the true point (not
   the noisy label), and only then given to the learner as a click. So every error is on an image
   the model had not learned from, as in use.

Compared: the calibration alone (what the tracker ran before) and the learner, as median on-screen
error in mm (and px of a 2560-px 27" monitor, 0.233 mm/px, the user's), over the clicks after the
first 100 (the learner needs some) and in bands of clicks so far.

    services/gaze/.venv/bin/python eval/click_learning.py ~/data/mpiifacegaze/cache \\
        --out eval/results/click-learning.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/gaze"))

from gaze.face import Face  # noqa: E402
from gaze.head import Camera, GazeModel, HeadEstimator, Poser, Screen, pack  # noqa: E402
from gaze.learn import Learner  # noqa: E402

CALIBRATION = 13
LABEL_MM = 20.0  # mean distance of a click from where the eyes were (PACE, after filtering)
USER_MM_PER_PX = 0.233  # 27", 2560 px
BANDS = ((0, 100), (100, 300), (300, 1000), (1000, 10**9))
WARMUP = 100
GATE: float | None = None  # --gate-k: override gaze.learn.GATE_K
HEAD_TERMS = True  # --no-head-terms: learn the eye terms only


class Estimator(HeadEstimator):
    """The head-aware estimator around a fitted model, without a camera."""

    def __init__(self, model: GazeModel, monitor: tuple[int, int]) -> None:
        super().__init__(monitor, model.screen.mm_per_px, landmarker=object())
        self.model = model


def person(path: Path, outliers: float, label_mm: float, seed: int) -> dict:
    d = dict(np.load(path))
    size = tuple(int(v) for v in d["image_size"])
    found = np.isfinite(d["points"][:, 0, 0])
    days, target = d["days"], d["target_px"].astype(float)
    counts = {day: int(((days == day) & found).sum()) for day in np.unique(days)}
    cal_day = next(day for day in sorted(counts) if counts[day] >= CALIBRATION + 10)
    cal = np.flatnonzero((days == cal_day) & found)[:CALIBRATION]
    later = np.flatnonzero((days > cal_day) & found)  # the days after calibration, in order
    mm_per_px = float(d["screen_mm"][0] / d["screen_px"][0])
    moved_mm = np.linalg.norm(d["face_center"] - d["face_center"][cal].mean(0), axis=1)  # head from calibration
    monitor = (int(d["screen_px"][0]), int(d["screen_px"][1]))

    poser = Poser(Camera.nominal(*size))
    heads = {int(i): poser.measure(Face(d["points"][i].astype(float), size)) for i in np.flatnonzero(found)}
    model = GazeModel(Screen.below_camera(monitor, float(d["screen_mm"][0]))).fit([heads[i] for i in cal], target[cal])
    static = GazeModel(model.screen, eyes=list(model.eyes))
    learner = Learner(Estimator(model, monitor), monitor, None, None, head_terms=HEAD_TERMS)
    if GATE is not None:
        learner.gate_k = GATE

    rng = np.random.default_rng(seed)
    sigma = label_mm / mm_per_px / np.sqrt(np.pi / 2)  # a 2-D Gaussian with this mean distance
    errors_static, errors_learned, outcomes = [], [], {"used": 0, "not looking": 0}
    for n, i in enumerate(later):
        vector = pack(heads[int(i)])
        truth = target[i]
        for errors, m in ((errors_static, static), (errors_learned, learner.estimator.model)):
            p = m.predict([heads[int(i)]])[0]
            errors.append(float(np.hypot(*(p - truth))) * mm_per_px)
        if rng.random() < outliers:  # clicked while looking elsewhere
            label = rng.uniform([0, 0], monitor)
        else:
            label = np.clip(truth + rng.normal(0, sigma, 2), [0, 0], np.array(monitor) - 1)
        t = float(days[i]) * 86400 + n * 5.0
        outcomes[learner.add(t, float(label[0]), float(label[1]), vector)] += 1
    s, e = np.array(errors_static), np.array(errors_learned)
    bands = {}
    for lo, hi in BANDS:
        sel = slice(lo, min(hi, len(s)))
        if lo < len(s):
            label = f"{lo}-{hi}" if hi < 10**9 else f"{lo}+"
            bands[label] = [round(float(np.nanmedian(s[sel])), 1), round(float(np.nanmedian(e[sel])), 1)]
    after = slice(WARMUP, None)
    return {
        "person": path.stem,
        "clicks": len(later),
        "days": int(len(np.unique(days[later]))),
        "calibration_mm": round(float(np.nanmedian(s[after])), 1),
        "learned_mm": round(float(np.nanmedian(e[after])), 1),
        "bands_mm": bands,
        "updates": learner.version,
        "outcomes": outcomes,
        "raw": (s[after], e[after], moved_mm[later][after]),
    }


def run(paths: list[Path], outliers: float, label_mm: float) -> dict:
    results = [person(p, outliers, label_mm, seed=k) for k, p in enumerate(paths)]
    s = np.concatenate([r["raw"][0] for r in results])
    e = np.concatenate([r["raw"][1] for r in results])
    moved = np.concatenate([r["raw"][2] for r in results])
    by_move = {}
    for lo, hi in ((0, 20), (20, 40), (40, 80), (80, 10**9)):
        sel = (moved >= lo) & (moved < hi)
        label = f"{lo}-{hi}" if hi < 10**9 else f"{lo}+"
        by_move[label] = [round(float(np.nanmedian(s[sel])), 1), round(float(np.nanmedian(e[sel])), 1), int(sel.sum())]
    better = sum(r["learned_mm"] < r["calibration_mm"] for r in results)
    worse = [r["person"] for r in results if r["learned_mm"] > r["calibration_mm"] * 1.02]
    for r in results:
        del r["raw"]
    return {
        "label_error_mm": label_mm,
        "looking_elsewhere": outliers,
        "median_mm": {"calibration": round(float(np.nanmedian(s)), 1), "learned": round(float(np.nanmedian(e)), 1)},
        "median_px_on_user_monitor": {
            "calibration": round(float(np.nanmedian(s)) / USER_MM_PER_PX, 0),
            "learned": round(float(np.nanmedian(e)) / USER_MM_PER_PX, 0),
        },
        "p90_mm": {
            "calibration": round(float(np.nanpercentile(s, 90)), 1),
            "learned": round(float(np.nanpercentile(e, 90)), 1),
        },  # fmt: skip
        "by_head_moved_mm": by_move,  # [calibration alone, learned, images]
        "people_better": f"{better} of {len(results)}",
        "people_worse_by_2pct": worse,
        "people": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cache", type=Path)
    parser.add_argument("--people", nargs="*")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--gate-k", type=float)
    parser.add_argument("--no-head-terms", action="store_true", help="Learn only the eye terms, as before")
    parser.add_argument("--runs", nargs="*", help="Only runs whose name starts with these (typical, bad, clean)")
    args = parser.parse_args()
    global GATE, HEAD_TERMS
    GATE = args.gate_k
    HEAD_TERMS = not args.no_head_terms
    paths = sorted(args.cache.glob("p*.npz"))
    if args.people:
        paths = [p for p in paths if p.stem in args.people]
    out = {"dataset": "MPIIFaceGaze", "calibration_images": CALIBRATION, "warmup_clicks": WARMUP, "runs": {}}
    for name, outliers, label in (
        ("typical: 15% looking elsewhere, 20 mm labels", 0.15, LABEL_MM),
        ("bad: 40% looking elsewhere, 40 mm labels", 0.40, 2 * LABEL_MM),
        ("clean: exact labels", 0.0, 0.0),
        ("adversarial: every click made without looking", 1.0, 0.0),
    ):
        if args.runs and not any(name.startswith(r) for r in args.runs):
            continue
        run_result = run(paths, outliers, label)
        out["runs"][name] = run_result
        print(name, json.dumps({k: v for k, v in run_result.items() if k != "people"}), flush=True)
    if args.out:
        args.out.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()

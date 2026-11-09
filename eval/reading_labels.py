"""Reading as labels for the gaze model: are the labels right, and does learning from them help?

1. **Are they right?** The receiver turns reading into labels (``capture/labels.py``): a line
   read between the line above and the line below, with the decoder sure of it. Simulated end to
   end (``eval/gaze_sim.py``: the eyes read real page layouts line by line, the webcam error model
   of the PC, the real tracker and receiver): each label's height against where the eyes really
   were, for the article and paragraph scenarios, at the recalibrated error (69 px median) and the
   first, coarse one (183 px).

2. **Does it help?** MPIIFaceGaze, as ``eval/click_learning.py`` replays it (15 people, their own
   laptops, days to weeks, calibrated on 13 images of the first day; every later image predicted
   before it is learned from). A share of the images are clicks (with 20 mm label noise and 15%
   made while looking elsewhere), the rest are reading-line labels: the image's true height, held
   to a 600 px span around its true x (the learner keeps its own x within the span), with a share
   of them put one text line off either way (wrong decoding), or every one a line lower (a decoder
   consistently astray). Compared on every image's error against where
   the eyes really were: the clicks alone, and the clicks with the lines.

    services/gaze/.venv/bin/python eval/reading_labels.py ~/data/mpiifacegaze/cache \\
        --out eval/results/reading-labels.json   # part 2; part 1 runs in the ingest environment:
    uv run python eval/reading_labels.py --simulate --out eval/results/reading-labels-sim.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "eval"), str(ROOT / "services/gaze"), str(ROOT / "services/capture-win")]

SCENARIOS = (
    "read an article",
    "read an article, 200% zoom",
    "re-read a paragraph 3 times",
    "slow read under a heading",
)
CLICK_SHARE = 0.15  # of the images, as clicks; the rest are reading lines (about six lines a click)
PITCH_MM = 8.4  # body text line pitch on the user's monitor (28.8 CSS px at 100%, 125% scaling, 0.233 mm/px)
SPAN_PX = 600
RUNS: list[str] = []  # --runs: only runs whose name starts with these


def simulate(seeds: int) -> dict:
    import gaze_sim as g

    out: dict = {}
    for profile in ("recalibrated", "first-calibration"):
        for name in SCENARIOS:
            scenario = next(s for s in g.SCENARIOS if s.name == name)
            errors: list[float] = []
            reading = pitch = 0.0
            for seed in range(g.SEED_BASE, g.SEED_BASE + seeds):
                result = g.run(scenario, g._model(profile), seed, g.Screen())
                errors += result.label_errors
                reading += result.reading_s
                pitch = result.label_line_px
            e = np.array(errors)
            out[f"{name} | {profile}"] = {
                "labels": len(e), "per_minute_of_reading": round(len(e) / max(reading, 1e-9) * 60, 1),
                "on_the_right_line": round(float((e < pitch / 2).mean()), 4) if len(e) else None,
                "one_line_off": round(float(((e >= pitch / 2) & (e < 1.5 * pitch)).mean()), 4) if len(e) else None,
                "line_pitch_px": pitch,
            }  # fmt: skip
            print(name, profile, json.dumps(out[f"{name} | {profile}"]), flush=True)
    return out


def person(path: Path, wrong: float, use_lines: bool, seed: int) -> dict:
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
    mm_per_px = float(d["screen_mm"][0] / d["screen_px"][0])
    monitor = (int(d["screen_px"][0]), int(d["screen_px"][1]))
    poser = Poser(Camera.nominal(*size))
    heads = {int(i): poser.measure(Face(d["points"][i].astype(float), size)) for i in np.flatnonzero(found)}
    model = GazeModel(Screen.below_camera(monitor, float(d["screen_mm"][0]))).fit([heads[i] for i in cal], target[cal])
    learner = Learner(cl.Estimator(model, monitor), monitor, None, None, settings=Settings())
    rng = np.random.default_rng(seed)
    sigma = cl.LABEL_MM / mm_per_px / np.sqrt(np.pi / 2)
    pitch = PITCH_MM / mm_per_px
    errors, kinds = [], []
    for n, i in enumerate(later):
        vector, truth = pack(heads[int(i)]), target[i]
        p = learner.estimator.model.predict([heads[int(i)]])[0]
        errors.append(float(np.hypot(*(p - truth))) * mm_per_px)
        t = float(days[i]) * 86400 + n * 5.0
        if rng.random() < CLICK_SHARE:
            kinds.append(0)
            if rng.random() < 0.15:
                label = rng.uniform([0, 0], monitor)
            else:
                label = np.clip(truth + rng.normal(0, sigma, 2), [0, 0], np.array(monitor) - 1)
            learner.add(t, float(label[0]), float(label[1]), vector)
        else:
            kinds.append(1)
            if use_lines:
                off = pitch if wrong >= 1.0 else pitch * rng.choice([-1, 1])  # all: one line lower, always
                y = truth[1] + (off if rng.random() < wrong else 0.0)
                x0 = max(0.0, truth[0] - SPAN_PX / 2 + rng.uniform(-150, 150))
                learner.add_line(t, float(y), x0, min(monitor[0] - 1.0, x0 + SPAN_PX), vector)
    e = np.array(errors)
    after = slice(int(cl.WARMUP / CLICK_SHARE), None)  # after about the first 100 clicks
    return {"person": path.stem, "error_mm": round(float(np.median(e[after])), 1), "updates": learner.version,
            "lines_used": learner.line_counts["used"], "raw": e[after]}  # fmt: skip


def learning(cache: Path) -> dict:
    paths = sorted(cache.glob("p*.npz"))
    out: dict = {"click_share": CLICK_SHARE, "pitch_mm": PITCH_MM, "runs": {}}
    for name, wrong, use in (
        ("clicks alone", 0.0, False),
        ("clicks and lines", 0.0, True),
        ("clicks and lines, 10% one line off", 0.10, True),
        ("clicks and lines, every line one line lower", 1.0, True),
    ):
        if RUNS and not any(name.startswith(r) for r in RUNS):
            continue
        people = [person(p, wrong, use, seed=k) for k, p in enumerate(paths)]
        everyone = np.concatenate([r.pop("raw") for r in people])
        out["runs"][name] = {
            "median_mm": round(float(np.median(everyone)), 1),
            "p90_mm": round(float(np.percentile(everyone, 90)), 1),
            "people": people,
        }
        print(name, out["runs"][name]["median_mm"], out["runs"][name]["p90_mm"], flush=True)
    if "clicks alone" not in out["runs"]:
        return out
    base = {r["person"]: r["error_mm"] for r in out["runs"]["clicks alone"]["people"]}
    for run in out["runs"].values():
        run["people_better"] = sum(r["error_mm"] < base[r["person"]] for r in run["people"])
        run["people_worse_by_2pct"] = [r["person"] for r in run["people"] if r["error_mm"] > 1.02 * base[r["person"]]]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cache", type=Path, nargs="?")
    parser.add_argument("--simulate", action="store_true", help="Part 1: label accuracy in the simulator")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--runs", nargs="*", default=[])
    args = parser.parse_args()
    RUNS[:] = args.runs
    result = simulate(args.seeds) if args.simulate else learning(args.cache)
    if args.out:
        args.out.write_text(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main()

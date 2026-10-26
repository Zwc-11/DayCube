"""A glasses walk simulated from a phone scan: how well images alone rebuild the walk.

A Stray Scanner scan is a video whose every frame has the phone's own pose (ARKit, metres, gravity
aligned). Glasses give images only. So the scan's video is cut the way glasses would capture it,
the poses are set aside, the images are reconstructed alone (``sfm``), and the result is scored
against the poses set aside:

- **Modes** (``MODES``). A photo every 3 s and every second (glasses stills), and a 5 frames-a-second
  stream at 720p's size (the glasses' live stream).
- **Placed.** The share of images the reconstruction placed.
- **Path error.** After a similarity fit to the phone's path: RMS, 90th percentile and largest,
  metres; and the cameras' turn error, degrees.
- **On the map with GPS only.** Gravity from each frame (as the glasses' IMU gives it; taken from
  ARKit here) levels the reconstruction, and a 2D similarity with a capture-time search fits it to
  the day's GPS. Reported: the residual, the offset found, and the scale's error against the scale
  the phone's path gives, which is how metric a glasses walk can be without LiDAR.
- **Splats** (``train``). Brush trains twice on each mode's placed images: with the reconstructed
  poses (in metres from the GPS scale), and with the phone's poses (the best those images could
  do). Both are scored on the same ``HELD_OUT`` frames that no mode trained on, given their true
  poses carried into each reconstruction's frame by the fit, so the PSNR difference is what the
  reconstructed poses cost.

Everything is written under ``work`` (the frames are the person's own: they stay on the PC).
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..skeleton.parsers.types import Fix
from ..splat import dataset
from . import align, sfm

MODES: dict[str, dict] = {
    "photo_3s": {"every_s": 3.0, "long_side": 1920, "label": "a photo every 3 s"},
    "photo_1s": {"every_s": 1.0, "long_side": 1920, "label": "a photo every second"},
    # Frames 25 cm apart share most of what they see: fewer features each, matched 4x faster.
    "stream_5fps": {"every_s": 0.2, "long_side": 1280, "label": "the 720p stream at 5 frames a second",
                    "features": 4096},
}
HELD_OUT = 24
MIN_HELD_OUT = 4  # fewer held-out frames inside the rebuilt stretch: no training
SETTLE_S = 0.5  # ARKit's first poses come before tracking settles
STILL_S = 0.15  # a photo is the sharpest video frame within this of its time
OPENGL = np.diag([1.0, -1.0, -1.0, 1.0])  # OpenCV camera axes to OpenGL's (and back)
DOWN = np.array([0.0, -1.0, 0.0])  # gravity in ARKit's frame (y up)


@dataclass
class Truth:
    t: np.ndarray  # (n,) seconds from the first frame
    c2w: np.ndarray  # (n, 4, 4) the phone's poses, OpenCV convention, metres
    K: np.ndarray  # (n, 4) fx, fy, cx, cy at the video's size
    width: int
    height: int


def read_truth(capture: Path, video: Path) -> Truth:
    width, height, count = dataset._video_size(video)
    odo = dataset.read_odometry(capture, width, height)
    n = min(count, len(odo.frame))
    return Truth(odo.t[:n], odo.c2w[:n] @ OPENGL, odo.K[:n], width, height)


def every(t: np.ndarray, step_s: float, start_s: float = SETTLE_S, sharp: np.ndarray | None = None,
          window_s: float = STILL_S) -> np.ndarray:  # fmt: skip
    """The frames nearest to every ``step_s`` from ``start_s`` on; with ``sharp`` (each frame's
    sharpness), the sharpest within ``window_s`` of each time instead. A glasses still is exposed on
    its own and comes out sharp; a video frame of a walk is often blurred."""
    wanted = np.arange(start_s, t[-1] + 1e-9, step_s)
    if sharp is None:
        picks = np.clip(np.searchsorted(t, wanted), 0, len(t) - 1)
    else:
        picks = []
        for want in wanted:
            lo, hi = np.searchsorted(t, want - window_s), np.searchsorted(t, want + window_s, side="right")
            hi = min(hi, len(sharp))
            picks.append(lo + int(np.argmax(sharp[lo:hi])) if hi > lo else min(lo, len(t) - 1))
        picks = np.array(picks, dtype=int)
    picks = np.unique(picks)
    return picks[t[picks] >= start_s - 1e-6]


def held_out(t: np.ndarray, count: int | None = None, avoid: list[np.ndarray] | None = None) -> np.ndarray:
    """``count`` (``HELD_OUT``) frames spread over the walk, each the nearest frame no photo mode
    trains on."""
    count = count or HELD_OUT
    span = np.linspace(SETTLE_S, t[-1], count + 2)[1:-1]
    picks = []
    taken = np.unique(np.concatenate(avoid)) if avoid else np.zeros(0, int)
    for want in span:
        i = int(np.clip(np.searchsorted(t, want), 0, len(t) - 1))
        for step in range(0, 30):  # the nearest frame no photo mode uses
            for j in (i + step, i - step):
                if 0 <= j < len(t) and j not in taken and j not in picks:
                    picks.append(j)
                    break
            else:
                continue
            break
    return np.array(sorted(picks), dtype=int)


def _size(truth: Truth, long_side: int) -> tuple[int, int]:
    scale = min(1.0, long_side / max(truth.width, truth.height))
    return round(truth.width * scale / 2) * 2, round(truth.height * scale / 2) * 2


def _link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def _frame(path: str, c2w_cv: np.ndarray, K: np.ndarray, size: tuple[int, int]) -> dict:
    fx, fy, cx, cy = (float(v) for v in K)
    return {
        "file_path": path,
        "transform_matrix": [[round(float(v), 7) for v in row] for row in c2w_cv @ OPENGL],
        "fl_x": fx, "fl_y": fy, "cx": cx, "cy": cy, "w": size[0], "h": size[1],
    }  # fmt: skip


def _dataset(folder: Path, images: list[Path], c2w: np.ndarray, K: np.ndarray, size: tuple[int, int],
             eval_images: list[Path], eval_c2w: np.ndarray, eval_K: np.ndarray,
             points: np.ndarray, colours: np.ndarray) -> Path:  # fmt: skip
    """A Brush training set: ``transforms.json`` (with ``init.ply``) and ``transforms_val.json``
    (the held-out frames Brush scores on), the images linked in."""
    if folder.exists():
        shutil.rmtree(folder)
    frames = []
    for k, (image, pose) in enumerate(zip(images, c2w, strict=True)):
        _link(image, folder / "images" / f"{k:05d}.jpg")
        frames.append(_frame(f"images/{k:05d}.jpg", pose, K, size))
    held = []
    for k, (image, pose) in enumerate(zip(eval_images, eval_c2w, strict=True)):
        _link(image, folder / "eval" / f"{k:05d}.jpg")
        held.append(_frame(f"eval/{k:05d}.jpg", pose, eval_K, size))
    dataset.write_ply(folder / "init.ply", points, colours)
    (folder / "transforms.json").write_text(json.dumps({"ply_file_path": "init.ply", "frames": frames}) + "\n")
    (folder / "transforms_val.json").write_text(json.dumps({"frames": held}) + "\n")
    return folder


def _carry(sim: tuple[float, np.ndarray, np.ndarray], c2w_true: np.ndarray) -> np.ndarray:
    """True poses (metres) into a reconstruction's own frame, through the fit ``sim`` that maps
    the reconstruction onto the truth (x_true = s R x + t)."""
    s, R, t = sim
    out = np.tile(np.eye(4), (len(c2w_true), 1, 1))
    out[:, :3, :3] = R.T @ c2w_true[:, :3, :3]
    out[:, :3, 3] = (c2w_true[:, :3, 3] - t) @ R / s
    return out


def _metric(c2w: np.ndarray, L: np.ndarray, scale: float) -> np.ndarray:
    """Poses levelled by ``L`` and scaled to metres."""
    out = c2w.copy()
    out[:, :3, :3] = L @ c2w[:, :3, :3]
    out[:, :3, 3] = scale * c2w[:, :3, 3] @ L.T
    return out


def _length(points: np.ndarray) -> float:
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum()) if len(points) > 1 else 0.0


def picks_of(name: str, truth: Truth, sharp: np.ndarray | None) -> np.ndarray:
    """A mode's frames: photos the sharpest near their times, a stream's as they come."""
    mode = MODES[name]
    return every(truth.t, mode["every_s"], sharp=sharp if name.startswith("photo") else None)


def run_mode(name: str, truth: Truth, video: Path, work: Path, *, fixes: list[Fix], t0: float,
             eval_picks: np.ndarray, train: bool, steps: int, mapper: str,
             trainer: Callable[..., dict] | None, say: Callable[[str], None],
             sharp: np.ndarray | None = None) -> dict:  # fmt: skip
    mode = MODES[name]
    picks = picks_of(name, truth, sharp)
    size = _size(truth, mode["long_side"])
    folder = work / name
    say(f"{name}: {len(picks)} frames at {size[0]}x{size[1]} ({mode['label']})")
    dataset.extract(video, picks, folder / "frames", size)
    result: dict = {"label": mode["label"], "images": len(picks), "size": list(size)}
    model = sfm.reconstruct(folder / "frames", folder / "colmap", mapper=mapper, max_features=mode.get("features", 8192),
                            say=say)  # fmt: skip
    if model is None:
        result.update(registered=0, registered_share=0.0)
        return result
    ks = np.array([int(Path(n).stem) for n in model.names])
    frames = picks[ks]
    true = truth.c2w[frames]
    error = align.trajectory_error(model.c2w, true)
    # The stretch of the walk the model covers (the largest model: others are pieces apart).
    first, last = int(frames.min()), int(frames.max())
    covered = _length(truth.c2w[first : last + 1, :3, 3])
    result.update(
        registered=len(ks), registered_share=round(len(ks) / len(picks), 3), models=model.models,
        covered_m=round(covered, 1), covered_share=round(covered / max(_length(truth.c2w[:, :3, 3]), 1e-9), 3),
        seconds=model.seconds, timings=model.timings, reprojection_px=model.reprojection_px,
        points=len(model.points), focal_px=round(float(model.K[0]), 1),
        focal_true_px=round(float(np.median(truth.K[frames, 0])) * size[0] / truth.width, 1),
        path={k: v for k, v in error.items() if k != "sim"},
    )  # fmt: skip
    # Gravity in each camera's own axes, as the glasses' IMU reports it (ARKit's here).
    down = np.einsum("nji,j->ni", true[:, :3, :3], DOWN)
    L = align.level(model.c2w, down)
    gps = align.georeference(model.c2w[:, :3, 3] @ L.T, truth.t[frames], t0, fixes) if fixes else None
    if gps:
        result["gps"] = {
            "rmse_m": gps["rmse_m"], "offset_s": gps["offset_s"],
            "scale_error_pct": round(100 * (gps["scale"] / error["scale"] - 1), 2),
        }  # fmt: skip
    if not train or trainer is None:
        return result
    scale = gps["scale"] if gps else error["scale"]
    images = [folder / "frames" / name_ for name_ in model.names]
    held = sorted((work / "held_out").glob("*.jpg"))
    eval_images = held if size == _size(truth, 1920) else sorted((work / f"held_out_{size[0]}").glob("*.jpg"))
    # Only held-out frames inside the stretch the model covers can be drawn from it.
    inside = (eval_picks > first) & (eval_picks < last)
    if inside.sum() < MIN_HELD_OUT:
        result["train"] = {"skipped": f"{int(inside.sum())} held-out frames inside the rebuilt stretch"}
        return result
    eval_picks = eval_picks[inside]
    eval_images = [image for image, keep in zip(eval_images, inside, strict=True) if keep]
    # The reconstruction in metres, and the held-out frames carried into it.
    rebuilt = _metric(model.c2w, L, scale)
    carried = _metric(_carry(error["sim"], truth.c2w[eval_picks]), L, scale)
    points = scale * model.points @ L.T
    recon = _dataset(folder / "train_recon", images, rebuilt, model.K, size, eval_images, carried, model.K,
                     points, model.colours)  # fmt: skip
    # The same images with the phone's poses and intrinsics: what they could give at best.
    k_true = truth.K[frames] * np.array([size[0] / truth.width, size[1] / truth.height] * 2)
    phone = _dataset(folder / "train_phone", images, true, np.median(k_true, 0), size, eval_images,
                     truth.c2w[eval_picks], np.median(k_true, 0), align.apply(error["sim"], model.points),
                     model.colours)  # fmt: skip
    result["train"] = {
        "held_out": len(eval_picks),
        "recon": trainer(recon, folder / "splat_recon", steps=steps, say=say),
        "phone": trainer(phone, folder / "splat_phone", steps=steps, say=say),
    }
    r, p = result["train"]["recon"].get("psnr_db"), result["train"]["phone"].get("psnr_db")
    if r is not None and p is not None:
        result["train"]["psnr_cost_db"] = round(p - r, 2)
    return result


def simulate(capture: Path, video: Path, work: Path, *, fixes: list[Fix], t0: float,
             modes: list[str] | None = None, train: bool = False, steps: int = 7000, mapper: str = "incremental",
             trainer: Callable[..., dict] | None = None, on_result: Callable[[dict], None] | None = None,
             say: Callable[[str], None] = print) -> dict:  # fmt: skip
    """Simulate each mode on the scan (``capture``: its odometry; ``video``: its frames); ``t0``:
    epoch seconds of the first frame; ``fixes``: the day's GPS. ``on_result`` gets the report after
    each mode, so a long run can be read while it goes."""
    modes = modes or list(MODES)
    truth = read_truth(capture, video)
    work.mkdir(parents=True, exist_ok=True)
    sharp = None
    if any(m.startswith("photo") for m in modes):
        say("Scoring the video's frames for sharpness (photos are the sharpest near their times)")
        sharp = dataset.sharpness(video, truth.width, truth.height)
    photos = [picks_of(m, truth, sharp) for m in modes if m.startswith("photo")]
    eval_picks = held_out(truth.t, avoid=photos)
    report: dict = {
        "frames": len(truth.t), "duration_s": round(float(truth.t[-1]), 1),
        "path_m": round(float(np.linalg.norm(np.diff(truth.c2w[:, :3, 3], axis=0), axis=1).sum()), 1),
        "video": [truth.width, truth.height], "held_out": len(eval_picks), "mapper": mapper,
        "modes": {},
    }  # fmt: skip
    if train:
        for long_side in sorted({MODES[m]["long_side"] for m in modes}):
            size = _size(truth, long_side)
            name = "held_out" if size == _size(truth, 1920) else f"held_out_{size[0]}"
            dataset.extract(video, eval_picks, work / name, size)
    for name in modes:
        report["modes"][name] = run_mode(name, truth, video, work, fixes=fixes, t0=t0, eval_picks=eval_picks,
                                         train=train, steps=steps, mapper=mapper, trainer=trainer, say=say,
                                         sharp=sharp)  # fmt: skip
        report["checks"] = checks(report)
        if on_result:
            on_result(report)
    return report


def checks(report: dict) -> dict:
    """The plan's checks per mode (docs/glasses.md): placed >= 90%, path error <= 0.5 m RMS, GPS
    residual <= 3 m, scale within 5%."""
    out = {}
    for name, r in report["modes"].items():
        if not r.get("registered"):
            out[name] = {"placed_90pct": False}
            continue
        c = {"placed_90pct": r["registered_share"] >= 0.9, "path_rmse_le_0_5m": r["path"]["rmse_m"] <= 0.5}
        if "gps" in r:
            c["gps_rmse_le_3m"] = r["gps"]["rmse_m"] <= 3.0
            c["scale_within_5pct"] = abs(r["gps"]["scale_error_pct"]) <= 5.0
        out[name] = c
    return out

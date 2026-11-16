"""The items pipeline on a standard real recording whose camera path and depth are measured: the TUM
RGB-D benchmark's ``freiburg3_long_office_household`` (Sturm et al., IROS 2012; CC BY 4.0), a hand-held
Kinect carried around two office desks for 87 s. Not in the repository:

    curl -LO https://cvg.cit.tum.de/rgbd/dataset/freiburg3/rgbd_dataset_freiburg3_long_office_household.tgz
    python eval/items_tum.py /path/rgbd_dataset_freiburg3_long_office_household --out /path/items --every 1.5

It stands in for a phone's LiDAR scan of a room (colour, depth and pose per frame) so that the
pipeline can be measured on a room full of things without a new recording: how many things it
finds, how often the same thing seen from several frames is joined, and whether each item's place,
seen again from every frame that observed it, falls inside the box found there.

``--poses`` leaves the measured camera path and depth out and takes both from the models, as a
plain video's shot gets them (Depth Anything 3 in ``services/poses``, metres from the depth model),
and says how far the cameras then stand from where the motion capture has them.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/ingest"))

from ingest.items import frames as sources  # noqa: E402
from ingest.items import models, run  # noqa: E402
from ingest.items.lift import Frame  # noqa: E402
from ingest.splat.dataset import rotation  # noqa: E402

K_FR3 = (535.4, 539.2, 320.1, 247.6)  # the freiburg3 Kinect, from the benchmark's calibration page
DEPTH_PER_M = 5000.0
PAIR_S = 0.02


def _table(path: Path) -> list[list[str]]:
    return [line.split() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]


def _nearest(times: np.ndarray, t: float) -> int:
    return int(np.argmin(np.abs(times - t)))


def frames(folder: Path, every_s: float, limit: int | None = None) -> dict[int, Frame]:
    import cv2

    rgb, depth, truth = _table(folder / "rgb.txt"), _table(folder / "depth.txt"), _table(folder / "groundtruth.txt")
    depth_t = np.array([float(r[0]) for r in depth])
    truth_t = np.array([float(r[0]) for r in truth])
    poses = np.array([[float(v) for v in r[1:8]] for r in truth])
    out: dict[int, Frame] = {}
    last, t0 = -1e9, float(rgb[0][0])
    for index, (stamp, name) in enumerate(rgb):
        t = float(stamp)
        if t - last < every_s:
            continue
        d, g = _nearest(depth_t, t), _nearest(truth_t, t)
        if abs(depth_t[d] - t) > PAIR_S or abs(truth_t[g] - t) > PAIR_S:
            continue
        last = t
        image = cv2.cvtColor(cv2.imread(str(folder / name)), cv2.COLOR_BGR2RGB)
        metres = cv2.imread(str(folder / depth[d][1]), cv2.IMREAD_UNCHANGED).astype(np.float32) / DEPTH_PER_M
        c2w = np.eye(4)
        c2w[:3, :3] = rotation(poses[g : g + 1, 3:7])[0]
        c2w[:3, 1:3] *= -1  # the benchmark's camera axes (y down, z forward) to OpenGL's
        c2w[:3, 3] = poses[g, :3]
        out[index] = Frame(index, t - t0, image, K_FR3, metres, metres > 0, "lidar", c2w)
        if limit and len(out) >= limit:
            break
    return out


def modelled(measured: dict[int, Frame]) -> tuple[dict[int, Frame], dict]:
    """The same pictures as one shot of a plain video, in the first one's camera, and the error of
    that against the measured path: no alignment, so a wrong metre counts."""
    was = list(measured.values())
    with models.Poses() as poses_of:
        poses = poses_of(np.stack([f.image for f in was]))
    metres, kind = models.model_depth(was[0].image)
    now = sources.posed(0, [(f.t, f.image) for f in was], poses, 0, metres, kind)
    true = [np.linalg.inv(was[0].c2w) @ f.c2w for f in was]
    off = [float(np.linalg.norm(a.c2w[:3, 3] - b[:3, 3])) for a, b in zip(now, true, strict=True)]
    turn = [float(np.degrees(np.arccos(np.clip((np.trace(a.c2w[:3, :3].T @ b[:3, :3]) - 1) / 2, -1, 1))))
            for a, b in zip(now, true, strict=True)]  # fmt: skip
    path = float(np.linalg.norm(np.diff([b[:3, 3] for b in true], axis=0), axis=1).sum())
    far = [float(np.linalg.norm(b[:3, 3])) for b in true]
    scale = float(np.median([np.linalg.norm(a.c2w[:3, 3]) / d for a, d in zip(now, far, strict=True) if d > 0.2]))
    error = {"path_m": round(path, 2), "camera_off_m_median": round(float(np.median(off)), 3),
             "camera_off_m_worst": round(max(off), 3), "turn_off_deg_median": round(float(np.median(turn)), 2),
             "metres_over_true": round(scale, 3), "focal_px": round(now[0].K[0], 1)}  # fmt: skip
    return {f.index: replace(g, index=f.index) for f, g in zip(was, now, strict=True)}, error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--every", type=float, default=1.5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-captions", action="store_true")
    parser.add_argument(
        "--poses", action="store_true", help="Camera path and depth from the models, not the measured ones"
    )
    parser.add_argument("--seen", type=Path, help="Keep what the models saw here, and use it when it exists: "
                        "the items are then made again without running the models")  # fmt: skip
    args = parser.parse_args()
    chosen = frames(args.folder, args.every, args.limit)
    print(f"{len(chosen)} frames, one every {args.every} s")
    if args.poses:
        chosen, error = modelled(chosen)
        print(json.dumps(error))
    seen = None
    if args.seen and args.seen.exists():
        seen = pickle.loads(args.seen.read_bytes())
    elif args.seen:
        seen = run.observe_frames(chosen, not args.no_captions)
        args.seen.write_bytes(pickle.dumps(seen))
    run.index_frames(chosen, args.out, {"kind": "scan", "file": args.folder.name, "every_s": args.every},
                     "model" if args.poses else "kinect", not args.no_captions, seen=seen)  # fmt: skip


if __name__ == "__main__":
    main()

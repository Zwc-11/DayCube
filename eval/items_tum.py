"""The items pipeline on a standard real recording whose camera path and depth are measured: the TUM
RGB-D benchmark's ``freiburg3_long_office_household`` (Sturm et al., IROS 2012; CC BY 4.0), a hand-held
Kinect carried around two office desks for 87 s. Not in the repository:

    curl -LO https://cvg.cit.tum.de/rgbd/dataset/freiburg3/rgbd_dataset_freiburg3_long_office_household.tgz
    python eval/items_tum.py /path/rgbd_dataset_freiburg3_long_office_household --out /path/items --every 1.5

It stands in for a phone's LiDAR scan of a room (colour, depth and pose per frame) so that the
pipeline can be measured on a room full of things without a new recording: how many things it
finds, how often the same thing seen from several frames is joined, and whether each item's place,
seen again from every frame that observed it, falls inside the box found there.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/ingest"))

from ingest.items import run  # noqa: E402
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--every", type=float, default=1.5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-captions", action="store_true")
    args = parser.parse_args()
    chosen = frames(args.folder, args.every, args.limit)
    print(f"{len(chosen)} frames, one every {args.every} s")
    run.index_frames(chosen, args.out, {"kind": "scan", "file": args.folder.name, "every_s": args.every}, "kinect",
                     not args.no_captions)  # fmt: skip


if __name__ == "__main__":
    main()

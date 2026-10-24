"""Does the blink detector call an open eye looking at the bottom of the screen closed?

The upper lid follows the eye down, so the lid gap shrinks as the gaze goes down the screen. A
detector whose open reference is one number (learned while looking wherever the person looked
most) calls the bottom of the screen a blink and drops those frames: the gaze then stops and
stays where it was, about the middle. Real webcam images: MPIIFaceGaze, 15 people on their own
laptops, every image taken while looking at a known point, with eyes open (the dataset's people
looked at each point and pressed a key). Every frame flagged here is a false blink.

Each person's images go through ``gaze.head.Blinks`` in the order they were taken, as a video
would, with the "long narrowing is not a blink" rule switched off (these are single images
seconds apart, not a video), so only the open reference is measured:

- ``fixed``: the reference as one number per eye (the iris ignored: its height always 0);
- ``follows the iris``: the reference following the iris's height in the eye (the tracker's).

Reported: the share of frames called closed, by quarter of the screen from the top.

    services/gaze/.venv/bin/python eval/blink_downgaze.py ~/data/mpiifacegaze/cache \\
        --out eval/results/blink-downgaze.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/gaze"))

from gaze.face import LEFT_IRIS, RIGHT_IRIS  # noqa: E402
from gaze.head import Blinks  # noqa: E402


def person(path: Path) -> dict:
    d = dict(np.load(path))  # one read: an NpzFile decompresses an array again on every access
    found = np.flatnonzero(np.isfinite(d["points"][:, 0, 0]))
    quarter = np.minimum((d["target_px"][found, 1] / d["screen_px"][1] * 4).astype(int), 3)
    out = {}
    for name, follow in (("fixed", False), ("follows the iris", True)):
        detector = Blinks(shallow_s=math.inf)
        flagged = []
        for i in found:
            points = d["points"][i].astype(float)
            if not follow:  # the iris at the corners' midpoint: no height, so a fixed reference
                points[[RIGHT_IRIS, LEFT_IRIS], :2] = points[[133, 362], :2] * 0.5 + points[[33, 263], :2] * 0.5
            flagged.append(detector(points, now=0.0))
            detector.previous = None  # images seconds apart: no frame-to-frame closing
        flagged = np.array(flagged)
        out[name] = [round(float(flagged[quarter == q].mean()), 3) for q in range(4)]
    return {"person": path.stem, "images": int(len(found)), "closed_by_quarter": out}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cache", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    people = [person(p) for p in sorted(args.cache.glob("p*.npz"))]
    summary = {
        name: [round(float(np.mean([p["closed_by_quarter"][name][q] for p in people])), 3) for q in range(4)]
        for name in ("fixed", "follows the iris")
    }
    print("share of open-eye frames called closed, screen quarters top to bottom:", json.dumps(summary))
    if args.out:
        args.out.write_text(
            json.dumps({"dataset": "MPIIFaceGaze", "summary": summary, "people": people}, indent=1) + "\n"
        )


if __name__ == "__main__":
    main()

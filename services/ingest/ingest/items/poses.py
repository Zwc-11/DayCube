"""Camera poses for a video's shots, by Depth Anything 3 (DA3-SMALL, Apache-2.0).

Run as a file in its own environment (``services/poses``), not imported: the package needs numpy 1
and OpenCV 4, the rest of DayCube numpy 2 and OpenCV 5. ``python poses.py <folder>`` loads the
model once and then answers each name it reads on its input: ``<folder>/<name>.npy`` holds the
pictures of one shot (N, H, W, 3; RGB bytes), and it writes ``<folder>/<name>.npz`` and the name
back on its output. In the answer are ``w2c`` (N, 3, 4; world to camera, OpenCV axes), ``K``
(N, 3, 3) and ``depth`` (N, h, w), the last two at the size the model worked at. Lengths are in
the model's own unit, not metres: ``ingest.items.frames`` sets the scale.
"""

from __future__ import annotations

import sys
from pathlib import Path

MODEL = "depth-anything/DA3-SMALL"


def main(folder: Path) -> None:
    answers, sys.stdout = sys.stdout, sys.stderr  # the package prints as it works; only answers go back
    import numpy as np
    import torch
    from depth_anything_3.api import DepthAnything3

    model = DepthAnything3.from_pretrained(MODEL).to("cuda" if torch.cuda.is_available() else "cpu").eval()
    for line in sys.stdin:
        file = folder / f"{line.strip()}.npy"
        with torch.inference_mode():
            seen = model.inference(list(np.load(file)))
        np.savez(file.with_suffix(".npz"), w2c=seen.extrinsics, K=seen.intrinsics, depth=seen.depth)
        print(line.strip(), file=answers, flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))

"""Per-person arrays for the training benchmark, from extract.py's cache, with DayCube's own geometry.

Runs in the eye tracker's environment (it needs gaze.head): for every image the head pose and the
eye measurements exactly as the tracker computes them (the camera's field of view assumed, the
screen centred under the camera), once from the full-size landmarks and once from the landmarks
found on the shrunken image.

    services/gaze/.venv/bin/python eval/gaze_train_prepare.py /path/cache /path/prepared
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/gaze"))

from gaze.face import Face  # noqa: E402
from gaze.head import Camera, Poser, Screen, pack  # noqa: E402


def vectors(points: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    poser = Poser(Camera.nominal(*size))
    found = np.isfinite(points[:, 0, 0])
    X = np.full((len(points), 26), np.nan)
    for i in np.flatnonzero(found):
        X[i] = pack(poser.measure(Face(points[i].astype(float), size)))
    return X, found


def main(cache: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for path in sorted(cache.glob("p*.npz")):
        d = np.load(path)
        size = tuple(int(v) for v in d["image_size"])
        X, found = vectors(d["points"], size)
        X_low, found_low = vectors(d["points_low"], size)
        screen = Screen.below_camera(tuple(int(v) for v in d["screen_px"]), float(d["screen_mm"][0]))
        order = np.lexsort((d["files"], d["days"]))  # time order: day, then file name
        np.savez_compressed(
            out / path.name,
            X=X[order], X_low=X_low[order], found=found[order], found_low=found_low[order],
            crops=d["crops"][order], crops_low=d["crops_low"][order],
            target_px=d["target_px"][order].astype(float), days=d["days"][order],
            distance_mm=d["face_center"][order][:, 2],
            screen_origin=screen.origin, screen_ex=screen.ex, screen_ey=screen.ey, mm_per_px=screen.mm_per_px,
            true_mm_per_px=float(d["screen_mm"][0] / d["screen_px"][0]), screen_px=d["screen_px"],
        )  # fmt: skip
        print(path.stem, int(found.sum()), int(found_low.sum()), len(found), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))

"""MediaPipe face landmarks for every MPIIFaceGaze image, cached for eval/head_bench.py.

MPIIFaceGaze (Zhang et al., CC BY-NC-SA 4.0, research use) is 15 people using their own laptops
over weeks, each image with the screen point they were looking at and the laptop's camera and
screen geometry. It is not in the repository: download and unzip it, then

    services/gaze/.venv/bin/python eval/head_bench_extract.py /path/to/MPIIFaceGaze /path/to/cache

writes one ``pXX.npz`` per person: the 478 landmarks of each image (pixels) and its annotation.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/gaze"))


def extract(dataset: Path, cache: Path, person: str) -> str:
    import cv2
    import scipy.io
    from gaze.face import Landmarker

    out = cache / f"{person}.npz"
    if out.exists():
        return f"{person}: cached"
    rows = [line.split() for line in (dataset / person / f"{person}.txt").read_text().splitlines() if line.strip()]
    landmarker = Landmarker(video=False)
    points = np.full((len(rows), 478, 3), np.nan, dtype=np.float32)
    for i, row in enumerate(rows):
        image = cv2.imread(str(dataset / person / row[0]))
        face = landmarker.detect(image) if image is not None else None
        if face is not None:
            points[i] = face.points
    landmarker.close()
    calibration = dataset / person / "Calibration"
    camera = scipy.io.loadmat(calibration / "Camera.mat")
    monitor = scipy.io.loadmat(calibration / "monitorPose.mat")
    screen = scipy.io.loadmat(calibration / "screenSize.mat")
    np.savez_compressed(
        out,
        files=np.array([row[0] for row in rows]),
        days=np.array([int(row[0].split("/")[0][3:]) for row in rows]),
        target_px=np.array([[float(row[1]), float(row[2])] for row in rows]),
        head_rvec=np.array([[float(v) for v in row[15:18]] for row in rows]),
        head_tvec=np.array([[float(v) for v in row[18:21]] for row in rows]),
        face_center=np.array([[float(v) for v in row[21:24]] for row in rows]),
        target_mm=np.array([[float(v) for v in row[24:27]] for row in rows]),
        points=points,
        image_size=np.array([1280, 720]),
        camera_matrix=camera["cameraMatrix"],
        dist_coeffs=camera["distCoeffs"],
        monitor_rvec=monitor["rvects"].ravel(),
        monitor_tvec=monitor["tvecs"].ravel(),
        screen_px=np.array([int(screen["width_pixel"][0, 0]), int(screen["height_pixel"][0, 0])]),
        screen_mm=np.array([float(screen["width_mm"][0, 0]), float(screen["height_mm"][0, 0])]),
    )
    found = int(np.isfinite(points[:, 0, 0]).sum())
    return f"{person}: {found}/{len(rows)} faces"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("cache", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    people = sorted(p.name for p in args.dataset.iterdir() if p.is_dir() and p.name.startswith("p"))
    with ProcessPoolExecutor(args.workers) as pool:
        for line in pool.map(extract, [args.dataset] * len(people), [args.cache] * len(people), people):
            print(line, flush=True)


if __name__ == "__main__":
    main()

"""MPIIFaceGaze -> per-person cache for the gaze training benchmark.

For every image: MediaPipe landmarks at the dataset's resolution (eyes about 59 px wide) and again on
the image shrunk to LOW_SCALE (eyes about 22 px wide: what DayCube's 640x480 webcam mode gives), and
both eyes cropped from each, turned level and scaled by the eye's own width.

    services/gaze/.venv/bin/python eval/gaze_train_extract.py /path/MPIIFaceGaze /path/cache --workers 2
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

LOW_SCALE = 0.37
CROP = (96, 60)  # width, height, native
CROP_LOW = (60, 36)
SPAN = 1.8  # crop width in eye widths


def eye_crops(gray: np.ndarray, points: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """(2, H, W) uint8: the person's right eye, then the left, each level and as wide as SPAN eye widths."""
    import cv2
    from gaze.face import LEFT_EYE, RIGHT_EYE

    W, H = size
    out = np.zeros((2, H, W), np.uint8)
    for e, (eye, sign) in enumerate(((RIGHT_EYE, 1.0), (LEFT_EYE, -1.0))):
        a, b = points[eye[0], :2].astype(float), points[eye[1], :2].astype(float)
        v = (b - a) * sign  # along the image's x
        w = float(np.hypot(*v))
        if not np.isfinite(w) or w < 2:
            continue
        c, s = v / w
        k = SPAN * w / W
        m = (a + b) / 2
        # output pixel (u, v) -> source: m + k * R * (u - W/2, v - H/2)
        M = np.array([[k * c, -k * s, m[0] - k * (c * W / 2 - s * H / 2)],
                      [k * s, k * c, m[1] - k * (s * W / 2 + c * H / 2)]])  # fmt: skip
        interp = cv2.INTER_AREA if k > 1.5 else cv2.INTER_LINEAR
        out[e] = cv2.warpAffine(gray, M, (W, H), flags=interp | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
    return out


def extract(dataset: Path, cache: Path, person: str) -> str:
    import cv2
    import scipy.io
    from gaze.face import Landmarker

    out = cache / f"{person}.npz"
    if out.exists():
        return f"{person}: cached"
    rows = [line.split() for line in (dataset / person / f"{person}.txt").read_text().splitlines() if line.strip()]
    n = len(rows)
    landmarker = Landmarker(video=False)
    points = np.full((n, 478, 3), np.nan, np.float32)
    points_low = np.full((n, 478, 3), np.nan, np.float32)
    crops = np.zeros((n, 2, CROP[1], CROP[0]), np.uint8)
    crops_low = np.zeros((n, 2, CROP_LOW[1], CROP_LOW[0]), np.uint8)
    for i, row in enumerate(rows):
        image = cv2.imread(str(dataset / person / row[0]))
        if image is None:
            continue
        face = landmarker.detect(image)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if face is not None:
            points[i] = face.points
            crops[i] = eye_crops(gray, face.points, CROP)
        small = cv2.resize(image, None, fx=LOW_SCALE, fy=LOW_SCALE, interpolation=cv2.INTER_AREA)
        face = landmarker.detect(small)
        if face is not None:
            points_low[i] = face.points / LOW_SCALE  # back in the full image's pixels, for the same camera
            crops_low[i] = eye_crops(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), face.points, CROP_LOW)
    landmarker.close()
    cal = dataset / person / "Calibration"
    camera = scipy.io.loadmat(cal / "Camera.mat")
    monitor = scipy.io.loadmat(cal / "monitorPose.mat")
    screen = scipy.io.loadmat(cal / "screenSize.mat")
    np.savez_compressed(
        out,
        files=np.array([row[0] for row in rows]),
        days=np.array([int(row[0].split("/")[0][3:]) for row in rows]),
        target_px=np.array([[float(row[1]), float(row[2])] for row in rows]),
        head_rvec=np.array([[float(v) for v in row[15:18]] for row in rows]),
        head_tvec=np.array([[float(v) for v in row[18:21]] for row in rows]),
        face_center=np.array([[float(v) for v in row[21:24]] for row in rows]),
        target_mm=np.array([[float(v) for v in row[24:27]] for row in rows]),
        points=points, points_low=points_low, crops=crops, crops_low=crops_low,
        image_size=np.array([1280, 720]),
        camera_matrix=camera["cameraMatrix"], dist_coeffs=camera["distCoeffs"],
        monitor_rvec=monitor["rvects"].ravel(), monitor_tvec=monitor["tvecs"].ravel(),
        screen_px=np.array([int(screen["width_pixel"][0, 0]), int(screen["height_pixel"][0, 0])]),
        screen_mm=np.array([float(screen["width_mm"][0, 0]), float(screen["height_mm"][0, 0])]),
    )  # fmt: skip
    return f"{person}: {int(np.isfinite(points[:, 0, 0]).sum())}/{n} faces, low {int(np.isfinite(points_low[:, 0, 0]).sum())}/{n}"


def main() -> None:
    parser = argparse.ArgumentParser()
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
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/gaze"))
    main()

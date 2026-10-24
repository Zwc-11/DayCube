"""Does the head-aware gaze model beat EyeTrax when the head is not where it was at calibration?

Real webcam images, not a simulation: MPIIFaceGaze, 15 people on their own laptops over days to
weeks (eval/head_bench_extract.py caches the face landmarks). For each person a calibration is
taken from one sitting (the first ``--calibration`` images of the first day with enough images),
then every image from the other days is predicted. Those days the person sat elsewhere, at another
distance, with the head turned differently: the situation a calibration made this morning meets in
the afternoon.

Methods:

- ``eyetrax``: what the tracker runs today (EyeTrax features, ridge to screen pixels);
- ``head``: gaze.head, with the camera and screen as the dataset measured them;
- ``head-assumed``: gaze.head with what DayCube can know on a user's desk: a nominal webcam field of
  view and the screen centred under the camera in its plane, only the screen's width in mm known.

Errors are on-screen millimetres (and pixels of a 1920-px-wide 24" monitor, 0.277 mm/px, for
comparison with the calibration's thresholds), split by how far the head is from where it was at
calibration, from the dataset's own head pose annotation.

    services/gaze/.venv/bin/python eval/head_bench.py /path/to/cache --calibration 13 20 40 \\
        --out eval/results/head-bench.json

Landmarks come from MediaPipe in image mode for every method, which is better for EyeTrax too
than the video mode the tracker ran it in (gaze/head.py, HeadEstimator).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/gaze"))

from gaze.face import Face  # noqa: E402
from gaze.head import Camera, GazeModel, Head, Poser, Screen  # noqa: E402

MONITOR_MM_PER_PX = 0.277  # 24", 1920 px
MOVE_BINS_MM = (0, 20, 40, 80, 1e9)
TURN_BINS_DEG = (0, 5, 10, 20, 1e9)


def eyetrax_features(points: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """EyeTrax 0.4's extract_features, from pixel landmarks (it works in MediaPipe's normalised
    coordinates: x/width, y/height, z/width)."""
    from eyetrax.constants import LEFT_EYE_INDICES, MUTUAL_INDICES, RIGHT_EYE_INDICES

    w, h = size
    all_points = (points / np.array([w, h, w])).astype(np.float32)
    left_corner, right_corner, top = all_points[33], all_points[263], all_points[10]
    eye_center = (left_corner + right_corner) / 2.0
    shifted = all_points - eye_center
    x_axis = right_corner - left_corner
    x_axis /= np.linalg.norm(x_axis) + 1e-9
    y_approx = top - eye_center
    y_approx /= np.linalg.norm(y_approx) + 1e-9
    y_axis = y_approx - np.dot(y_approx, x_axis) * x_axis
    y_axis /= np.linalg.norm(y_axis) + 1e-9
    z_axis = np.cross(x_axis, y_axis)
    z_axis /= np.linalg.norm(z_axis) + 1e-9
    R = np.column_stack((x_axis, y_axis, z_axis))
    rotated = (R.T @ shifted.T).T
    dist = np.linalg.norm(R.T @ (right_corner - eye_center) - R.T @ (left_corner - eye_center))
    if dist > 1e-7:
        rotated /= dist
    features = rotated[LEFT_EYE_INDICES + RIGHT_EYE_INDICES + MUTUAL_INDICES].flatten()
    yaw = np.arctan2(R[1, 0], R[0, 0])
    pitch = np.arctan2(-R[2, 0], np.sqrt(R[2, 1] ** 2 + R[2, 2] ** 2))
    roll = np.arctan2(R[2, 1], R[2, 2])
    return np.concatenate([features, [yaw, pitch, roll]])


def _rotation_deg(rvecs: np.ndarray, reference: np.ndarray) -> np.ndarray:
    import cv2

    ref, _ = cv2.Rodrigues(reference)
    out = []
    for r in rvecs:
        m, _ = cv2.Rodrigues(r)
        cos = (np.trace(ref.T @ m) - 1) / 2
        out.append(np.degrees(np.arccos(np.clip(cos, -1, 1))))
    return np.array(out)


def _mean_rotation(rvecs: np.ndarray) -> np.ndarray:
    import cv2

    mats = np.array([cv2.Rodrigues(r)[0] for r in rvecs]).mean(0)
    u, _, vt = np.linalg.svd(mats)
    return cv2.Rodrigues(u @ vt)[0].ravel()


def person(path: Path, calibration: int) -> dict:
    import cv2
    from eyetrax.models import create_model

    d = dict(np.load(path))
    size = tuple(int(v) for v in d["image_size"])
    found = np.isfinite(d["points"][:, 0, 0])
    days, target = d["days"], d["target_px"].astype(float)
    counts = {day: int(((days == day) & found).sum()) for day in np.unique(days)}
    cal_day = next(day for day in sorted(counts) if counts[day] >= calibration + 10)
    cal = np.flatnonzero((days == cal_day) & found)[:calibration]
    test = np.flatnonzero((days != cal_day) & found)
    same = np.flatnonzero((days == cal_day) & found)[calibration:]
    s_true = float(d["screen_mm"][0] / d["screen_px"][0])

    # Head displacement from the calibration sitting, from the dataset's annotation.
    centre = d["face_center"][cal].mean(0)
    moved = np.linalg.norm(d["face_center"] - centre, axis=1)
    turned = _rotation_deg(d["head_rvec"], _mean_rotation(d["head_rvec"][cal]))

    predictions: dict[str, np.ndarray] = {}

    # EyeTrax, as the tracker runs it.
    X = np.zeros((len(found), len(eyetrax_features(d["points"][np.argmax(found)].astype(float), size))))
    for i in np.flatnonzero(found):
        X[i] = eyetrax_features(d["points"][i].astype(float), size)
    ridge = create_model("ridge")
    ridge.train(X[cal], target[cal])
    predictions["eyetrax"] = ridge.predict(X)

    # Head-aware with the measured camera and screen.
    K, dist = d["camera_matrix"], d["dist_coeffs"]
    true_camera = Camera(float(K[0, 0] + K[1, 1]) / 2, float(K[0, 2]), float(K[1, 2]))
    Rm, _ = cv2.Rodrigues(d["monitor_rvec"])
    true_screen = Screen(d["monitor_tvec"].astype(float), Rm[:, 0], Rm[:, 1], s_true)
    undistorted = d["points"].astype(float).copy()
    flat = undistorted[found][:, :, :2].reshape(-1, 1, 2)
    undistorted[found, :, :2] = cv2.undistortPoints(flat, K, dist, P=K).reshape(-1, 478, 2)
    assumed_camera = Camera.nominal(*size)
    assumed_screen = Screen.below_camera(tuple(int(v) for v in d["screen_px"]), float(d["screen_mm"][0]))
    for name, camera, screen, points in (
        ("head", true_camera, true_screen, undistorted),
        ("head-assumed", assumed_camera, assumed_screen, d["points"].astype(float)),
    ):
        poser = Poser(camera)
        heads: list[Head | None] = [
            poser.measure(Face(p, size)) if ok else None for p, ok in zip(points, found, strict=True)
        ]
        model = GazeModel(screen).fit([heads[i] for i in cal], target[cal])
        out = np.full_like(target, np.nan)
        idx = np.flatnonzero(found)
        out[idx] = model.predict([heads[i] for i in idx])
        predictions[name] = out

    result: dict = {"person": path.stem, "calibration_day": int(cal_day), "test_images": int(len(test)), "methods": {}}
    for name, pred in predictions.items():
        err_mm = np.linalg.norm(pred - target, axis=1) * s_true
        row = {
            "same_sitting_mm": float(np.nanmedian(err_mm[same])) if len(same) else None,
            "other_days_mm": float(np.nanmedian(err_mm[test])),
            "other_days_p90_mm": float(np.nanpercentile(err_mm[test], 90)),
            "failed": int(np.isnan(err_mm[test]).sum()),
            "by_move_mm": {},
            "by_turn_deg": {},
        }
        for key, values, bins in (("by_move_mm", moved, MOVE_BINS_MM), ("by_turn_deg", turned, TURN_BINS_DEG)):
            for lo, hi in zip(bins[:-1], bins[1:], strict=True):
                sel = test[(values[test] >= lo) & (values[test] < hi)]
                row[key][f"{lo:g}-{hi:g}" if hi < 1e9 else f"{lo:g}+"] = (
                    [float(np.nanmedian(err_mm[sel])), int(len(sel))] if len(sel) else [None, 0]
                )
        result["methods"][name] = row
    result["errors"] = {
        name: (np.linalg.norm(pred - target, axis=1) * s_true)[test].tolist() for name, pred in predictions.items()
    }
    # Signed errors with each day's median offset removed: what is left once a slowly changing
    # offset is corrected online (the receiver's click anchors do this).
    result["axes"], result["debiased"] = {}, {}
    for name, pred in predictions.items():
        signed = (pred - target)[test] * s_true
        result["axes"][name] = np.nanmedian(np.abs(signed), 0).tolist()
        debiased = signed.copy()
        for day in np.unique(days[test]):
            sel = days[test] == day
            debiased[sel] -= np.nanmedian(signed[sel], 0)
        result["debiased"][name] = np.linalg.norm(debiased, axis=1).tolist()
    result["moved"] = moved[test].tolist()
    result["turned"] = turned[test].tolist()
    return result


def summary(results: list[dict]) -> dict:
    methods = list(results[0]["methods"])
    out: dict = {"people": len(results), "methods": {}}
    for name in methods:
        errors = np.concatenate([r["errors"][name] for r in results])
        moved = np.concatenate([r["moved"] for r in results])
        turned = np.concatenate([r["turned"] for r in results])
        same = [
            r["methods"][name]["same_sitting_mm"] for r in results if r["methods"][name]["same_sitting_mm"] is not None
        ]
        row = {
            "same_sitting_mm": float(np.median(same)),
            "other_days_mm": float(np.nanmedian(errors)),
            "other_days_x_mm": float(np.median([r["axes"][name][0] for r in results])),
            "other_days_y_mm": float(np.median([r["axes"][name][1] for r in results])),
            "other_days_offset_removed_mm": float(np.nanmedian(np.concatenate([r["debiased"][name] for r in results]))),
            "other_days_p90_mm": float(np.nanpercentile(errors, 90)),
            "other_days_px_at_1920": float(np.nanmedian(errors) / MONITOR_MM_PER_PX),
            "per_person_mm": [round(r["methods"][name]["other_days_mm"], 1) for r in results],
            "by_move_mm": {},
            "by_turn_deg": {},
        }
        for key, values, bins in (("by_move_mm", moved, MOVE_BINS_MM), ("by_turn_deg", turned, TURN_BINS_DEG)):
            for lo, hi in zip(bins[:-1], bins[1:], strict=True):
                sel = (values >= lo) & (values < hi)
                label = f"{lo:g}-{hi:g}" if hi < 1e9 else f"{lo:g}+"
                row[key][label] = (
                    [round(float(np.nanmedian(errors[sel])), 1), int(sel.sum())] if sel.any() else [None, 0]
                )
        out["methods"][name] = row
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cache", type=Path)
    parser.add_argument("--calibration", type=int, nargs="+", default=[20], help="Images in the calibration sitting")
    parser.add_argument("--people", nargs="*", help="Only these (p00 ...)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    paths = sorted(args.cache.glob("p*.npz"))
    if args.people:
        paths = [p for p in paths if p.stem in args.people]
    out = {}
    for calibration in args.calibration:
        results = []
        for path in paths:
            r = person(path, calibration)
            results.append(r)
            line = "  ".join(f"{k} {v['other_days_mm']:.0f}" for k, v in r["methods"].items())
            print(f"{r['person']}: {line} mm (other days, {r['test_images']} images)", flush=True)
        s = summary(results)
        print(f"calibration from {calibration} images:")
        for name, row in s["methods"].items():
            print(
                f"{name:>13}: same sitting {row['same_sitting_mm']:.0f} mm, other days {row['other_days_mm']:.0f} mm "
                f"(x {row['other_days_x_mm']:.0f}, y {row['other_days_y_mm']:.0f}; p90 {row['other_days_p90_mm']:.0f}; "
                f"{row['other_days_px_at_1920']:.0f} px at 1920), each day's offset removed "
                f"{row['other_days_offset_removed_mm']:.0f} mm"
            )
            print(f"{'':>15}by head move: {row['by_move_mm']}")
            print(f"{'':>15}by head turn: {row['by_turn_deg']}")
        out[str(calibration)] = s
    if args.out:
        args.out.write_text(json.dumps({"dataset": "MPIIFaceGaze", "calibration_images": out}, indent=1) + "\n")


if __name__ == "__main__":
    main()

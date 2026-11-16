"""Putting a reconstruction from images alone where it belongs, and saying how far off it is.

A reconstruction from images (``sfm``) comes in a frame and a scale of its own. Two fits place it:

- **Against the truth** (``trajectory_error``). A similarity (rotation, shift and scale; Umeyama
  1991) from the reconstructed camera centres to the phone's own path (ARKit, metres): the residual
  is the path error, its scale says how many metres a reconstruction unit is, and the camera
  rotations, carried through the same fit, give the turn error.
- **Against the GPS** (``georeference``), which is all glasses have: their IMU gives gravity but no
  position. Gravity levels the reconstruction (``level``); then a 2D similarity from its
  horizontal path to the phone's GPS track, searched over a capture-time offset (glasses stamp a
  photo when it reaches the phone, not when it was taken), gives where it is, which way it faces
  and how big it is. The scale this gives against the scale from the truth is how well GPS alone
  makes a glasses walk metric.
"""

from __future__ import annotations

import math

import numpy as np

from ..phone.geo import EARTH_M, local
from ..skeleton.parsers.types import Fix

ALIGN_S = 20  # capture-time offsets searched, seconds either way


def umeyama(src: np.ndarray, dst: np.ndarray, *, scale: bool = True) -> tuple[float, np.ndarray, np.ndarray]:
    """``s, R, t`` with ``dst ~ s R src + t`` in least squares (no reflection), for (n, d) points."""
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    cov = b.T @ a / len(src)
    u, d, vt = np.linalg.svd(cov)
    sign = np.ones(len(d))
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        sign[-1] = -1.0
    R = u @ np.diag(sign) @ vt
    var = (a**2).sum() / len(src)
    s = float((d * sign).sum() / var) if scale and var > 0 else 1.0
    t = mu_d - s * R @ mu_s
    return s, R, t


def apply(sim: tuple[float, np.ndarray, np.ndarray], points: np.ndarray) -> np.ndarray:
    s, R, t = sim
    return s * np.asarray(points, float) @ R.T + t


def rotation_angle_deg(R: np.ndarray) -> np.ndarray:
    """The angle of rotation matrices (..., 3, 3), degrees."""
    trace = np.trace(R, axis1=-2, axis2=-1)
    return np.degrees(np.arccos(np.clip((trace - 1) / 2, -1.0, 1.0)))


def trajectory_error(estimate_c2w: np.ndarray, true_c2w: np.ndarray) -> dict:
    """The fit of reconstructed cameras (n, 4, 4; both in the same camera convention) to the true
    ones: path error (metres, after the similarity), turn error (degrees) and the scale (metres
    per reconstruction unit). ``sim`` maps reconstruction points into the true frame."""
    est = estimate_c2w[:, :3, 3]
    true = true_c2w[:, :3, 3]
    sim = umeyama(est, true)
    s, R, _ = sim
    error = np.linalg.norm(apply(sim, est) - true, axis=1)
    turned = R @ estimate_c2w[:, :3, :3]  # the cameras' rotations in the true frame
    turn = rotation_angle_deg(np.swapaxes(turned, -1, -2) @ true_c2w[:, :3, :3])
    length = float(np.linalg.norm(np.diff(true, axis=0), axis=1).sum())
    return {
        "sim": sim,
        "scale": s,
        "path_m": round(length, 1),
        "rmse_m": round(float(np.sqrt(np.mean(error**2))), 3),
        "p90_m": round(float(np.percentile(error, 90)), 3),
        "max_m": round(float(error.max()), 3),
        "turn_median_deg": round(float(np.median(turn)), 2),
        "turn_p90_deg": round(float(np.percentile(turn, 90)), 2),
    }


def level(c2w: np.ndarray, down_in_camera: np.ndarray) -> np.ndarray:
    """The rotation that turns a reconstruction upright (y up, as ARKit's frame), from gravity seen
    by each camera (``down_in_camera``: (n, 3), the IMU's down in the camera's own axes)."""
    down = (c2w[:, :3, :3] @ down_in_camera[..., None])[..., 0]
    g = down.mean(0)
    g /= np.linalg.norm(g)
    target = np.array([0.0, -1.0, 0.0])
    v = np.cross(g, target)
    c = float(g @ target)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1 + c)


def georeference(centres_level: np.ndarray, t: np.ndarray, t0: float, fixes: list[Fix],
                 align_s: int = ALIGN_S) -> dict | None:  # fmt: skip
    """Fit a levelled reconstruction (camera centres, y up; ``t``: seconds from ``t0``, epoch
    seconds) to the GPS: the capture-time offset (searched in whole seconds within ``align_s``),
    the 2D similarity and its residual. ``scale`` is metres per reconstruction unit.

    Offsets are compared by the residual against how far the GPS moved over them: with a free
    scale, an offset that lands on the GPS standing still (before or after the walk) fits a point,
    the walk shrunk to nothing, with almost no residual."""
    near = [f for f in fixes if t0 + t[0] - align_s - 5 <= f.t.timestamp() <= t0 + t[-1] + align_s + 5]
    if len(near) < 5 or len(t) < 3:
        return None
    lat0, lon0 = near[0].lat, near[0].lon
    ft = np.array([f.t.timestamp() for f in near])
    en = np.array([local(f.lat, f.lon, lat0, lon0) for f in near])
    top = np.column_stack([centres_level[:, 0], -centres_level[:, 2]])  # seen from above: x east-ish, -z north-ish
    fits = {}
    for offset in range(-align_s, align_s + 1):
        times = t0 + offset + t
        if times[0] < ft[0] or times[-1] > ft[-1]:
            continue
        gps = np.column_stack([np.interp(times, ft, en[:, 0]), np.interp(times, ft, en[:, 1])])
        spread = float(np.sqrt(np.mean(np.sum((gps - gps.mean(0)) ** 2, axis=1))))
        if spread < 1.0:  # the GPS hardly moved: nothing to fit a walk to
            continue
        sim = umeyama(top, gps)
        rmse = float(np.sqrt(np.mean(np.sum((apply(sim, top) - gps) ** 2, axis=1))))
        fits[offset] = (sim, rmse, rmse / spread)
    if not fits:
        return None
    offset = min(fits, key=lambda k: fits[k][2])
    (s, R, shift), rmse, _ = fits[offset]
    placed = apply((s, R, shift), top)
    lat = lat0 + np.degrees(placed[:, 1] / EARTH_M)
    lon = lon0 + np.degrees(placed[:, 0] / (EARTH_M * math.cos(math.radians(lat0))))
    return {
        "offset_s": offset,
        "rmse_m": round(rmse, 2),
        "scale": s,
        "rotation_deg": round(math.degrees(math.atan2(R[1, 0], R[0, 0])), 1),
        "latlon": np.column_stack([lat, lon]),
    }

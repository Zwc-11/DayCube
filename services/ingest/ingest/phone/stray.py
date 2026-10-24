"""A Stray Scanner capture (iPhone LiDAR app) as a video moment with its camera's path on the map.

A capture is ``rgb.mp4`` (the camera, 1920x1440 at 30 frames/s), ``odometry.csv`` (ARKit's pose
of every frame: device-uptime time, frame, position x y z in metres with y up, orientation
quaternion, intrinsics), ``camera_matrix.csv``, ``imu.csv`` and, when exported, ``depth/`` and
``confidence/`` (one PNG per frame, from which a 3D scene can be fused).

**When.** The video's ``creation_time`` is when recording began: on 2026-10-24 the camera's own
path (ARKit) matched the phone's GPS track best with the video starting 1 s after it (speed
correlation 0.42, against -0.01 taking it as the end). The start is then refined by matching the
two paths' shapes within ``ALIGN_S``, when a shifted start fits clearly better.

**Where.** ARKit's positions are metres in a frame of its own, gravity aligned, so the horizontal
path (x, -z) differs from metres east and north (from the GPS) by a rotation and a shift. These are
fitted by least squares (Kabsch, no scaling) to the GPS fixes over the capture; with the residual
under ``MAX_FIT_M`` the camera's every position and heading is put on the map, to a few metres.

Keyframes (one every ``KEYFRAME_S``, 640 px wide JPEG) let the moment be browsed without the video.
"""

from __future__ import annotations

import csv
import math
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from ..moments.media import executable, probe
from ..skeleton.parsers.types import Fix
from .geo import EARTH_M, local

KEYFRAME_S = 8.0
ALIGN_S = 20
SHIFTED = 0.8  # a shifted start must fit with under 80% of the stamped start's residual
MAX_FIT_M = 15.0
PATH_EVERY = 10  # frames between camera path points kept


@dataclass
class Scan:
    video: Path
    t0: datetime  # UTC, when the first frame was taken
    duration_s: float
    width: int
    height: int
    fov_deg: float  # horizontal
    t: np.ndarray  # seconds from the first frame, per odometry row
    position: np.ndarray  # (n, 3) ARKit metres, y up
    rotation: np.ndarray  # (n, 4) quaternions x y z w
    depth: Path | None = None
    stamped: str = "creation time, taken as the start"
    fit: dict | None = None  # georeferencing: offset_s, rmse_m, rotation_deg, origin
    geo: np.ndarray | None = field(default=None, repr=False)  # (n, 2) lat, lon per odometry row


def find(paths: list[Path]) -> list[dict[str, Path]]:
    """Stray Scanner captures among ``paths``: folders holding rgb.mp4 and odometry.csv, or the
    loose files (names may carry a prefix, as attachments do)."""
    captures: list[dict[str, Path]] = []
    loose: dict[str, Path] = {}
    for path in paths:
        if path.is_dir() and (path / "odometry.csv").is_file() and (path / "rgb.mp4").is_file():
            files = {"video": path / "rgb.mp4", "odometry": path / "odometry.csv"}
            if (path / "camera_matrix.csv").is_file():
                files["camera"] = path / "camera_matrix.csv"
            if (path / "depth").is_dir():
                files["depth"] = path / "depth"
            captures.append(files)
            continue
        name = path.name.lower()
        for key, suffix in (("video", "rgb.mp4"), ("odometry", "odometry.csv"), ("camera", "camera_matrix.csv")):
            if name == suffix or name.endswith("-" + suffix) or name.endswith("_" + suffix):
                loose[key] = path
        if path.is_dir() and path.name == "depth":
            loose["depth"] = path
    if "video" in loose and "odometry" in loose:
        captures.append(loose)
    return captures


def read(files: dict[str, Path], *, start: datetime | None = None) -> Scan:
    info = probe(files["video"])
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    duration = float(info["format"]["duration"])
    created = (info["format"].get("tags") or {}).get("creation_time")
    if start is None and not created:
        raise ValueError(f"{files['video'].name}: no creation time; give the start time")
    t0 = start.astimezone(UTC) if start else datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(UTC)
    rows = []
    with files["odometry"].open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = [h.strip() for h in next(reader)]
        index = {name: header.index(name) for name in ("timestamp", "x", "y", "z", "qx", "qy", "qz", "qw")}
        fx_at = header.index("fx") if "fx" in header else None
        for row in reader:
            if len(row) < len(index):
                continue
            rows.append([float(row[index[k]]) for k in ("timestamp", "x", "y", "z", "qx", "qy", "qz", "qw")]
                        + [float(row[fx_at]) if fx_at is not None and row[fx_at].strip() else math.nan])  # fmt: skip
    data = np.array(rows)
    width, height = int(video["width"]), int(video["height"])
    fx = float(np.nanmedian(data[:, 8])) if np.isfinite(data[:, 8]).any() else _camera_fx(files.get("camera"))
    fov = math.degrees(2 * math.atan(width / 2 / fx)) if fx and fx > 0 else 60.0
    return Scan(files["video"], t0, duration, width, height, round(fov, 1), data[:, 0] - data[0, 0], data[:, 1:4],
                data[:, 4:8], files.get("depth"), "given" if start else "creation time, taken as the start")  # fmt: skip


def _camera_fx(path: Path | None) -> float | None:
    if path is None:
        return None
    first = path.read_text(encoding="utf-8").splitlines()[0]
    return float(first.split(",")[0])


def georeference(scan: Scan, fixes: list[Fix]) -> dict | None:
    """Fit ARKit's horizontal path to the GPS fixes over the capture (start refined within
    ``ALIGN_S``); sets ``scan.geo`` and ``scan.fit`` when the fit is good."""
    t0 = scan.t0.timestamp()
    near = [f for f in fixes if t0 - ALIGN_S - 5 <= f.t.timestamp() <= t0 + scan.duration_s + ALIGN_S + 5]
    if len(near) < 10:
        return None
    lat0, lon0 = near[0].lat, near[0].lon
    ft = np.array([f.t.timestamp() for f in near])
    en = np.array([local(f.lat, f.lon, lat0, lon0) for f in near])
    ar = np.column_stack([scan.position[:, 0], -scan.position[:, 2]])  # x right, -z forward: seen from above
    sample = np.arange(0, len(scan.t), 5)
    fits = {}
    for offset in range(-ALIGN_S, ALIGN_S + 1):
        times = t0 + offset + scan.t[sample]
        if times[0] < ft[0] or times[-1] > ft[-1]:
            continue
        gps = np.column_stack([np.interp(times, ft, en[:, 0]), np.interp(times, ft, en[:, 1])])
        fits[offset] = _kabsch(ar[sample], gps)
    if not fits:
        return None
    # A start other than the stamped one only when the shapes say so clearly: along a straight
    # stretch every start fits about as well, and the stamp is right to a second or two.
    offset = min(fits, key=lambda k: fits[k][2])
    if 0 in fits and fits[offset][2] > SHIFTED * fits[0][2]:
        offset = 0
    R, shift, rmse = fits[offset]
    if rmse > MAX_FIT_M:
        return None
    placed = ar @ R.T + shift
    lat = lat0 + np.degrees(placed[:, 1] / EARTH_M)
    lon = lon0 + np.degrees(placed[:, 0] / (EARTH_M * math.cos(math.radians(lat0))))
    scan.geo = np.column_stack([lat, lon])
    scan.t0 = scan.t0 + timedelta(seconds=offset)
    scan.fit = {"offset_s": offset, "rmse_m": round(float(rmse), 2),
                "rotation_deg": round(math.degrees(math.atan2(R[1, 0], R[0, 0])), 1)}  # fmt: skip
    return scan.fit


def _kabsch(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """The rotation and shift taking points ``a`` onto ``b`` (2D, least squares, no scaling or
    reflection), and the residual RMS."""
    ca, cb = a.mean(0), b.mean(0)
    u, _, vt = np.linalg.svd((a - ca).T @ (b - cb))
    d = np.sign(np.linalg.det(vt.T @ u.T))
    R = vt.T @ np.diag([1.0, d]) @ u.T
    shift = cb - ca @ R.T
    rmse = float(np.sqrt(np.mean(np.sum((a @ R.T + shift - b) ** 2, axis=1))))
    return R, shift, rmse


def heading(scan: Scan) -> np.ndarray:
    """Compass heading (degrees from north) the camera looked along, per odometry row (needs
    ``georeference``)."""
    x, y, z, w = scan.rotation.T
    # The camera looks along its -z axis; rotated into ARKit's frame.
    fx = -(2 * (x * z + w * y))
    fz = -(1 - 2 * (x * x + y * y))
    forward = np.column_stack([fx, -fz])  # seen from above, as the path
    if scan.fit is None:
        return np.full(len(forward), np.nan)
    a = math.radians(scan.fit["rotation_deg"])
    east = forward[:, 0] * math.cos(a) - forward[:, 1] * math.sin(a)
    north = forward[:, 0] * math.sin(a) + forward[:, 1] * math.cos(a)
    return (np.degrees(np.arctan2(east, north)) + 360) % 360


def keyframes(scan: Scan, folder: Path, every_s: float = KEYFRAME_S) -> list[dict]:
    """JPEG keyframes in ``folder/keyframes``; returns [{file, t}]."""
    target = folder / "keyframes"
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("kf_*.jpg"):
        old.unlink()
    subprocess.run(
        [executable("ffmpeg"), "-nostdin", "-v", "error", "-i", str(scan.video), "-vf",
         f"fps=1/{every_s},scale=640:-2", "-q:v", "4", str(target / "kf_%03d.jpg")],
        check=True, capture_output=True, timeout=600,
    )  # fmt: skip
    # The fps filter takes the frame at the middle of each interval.
    return [{"file": f"keyframes/{p.name}", "t": round((k + 0.5) * every_s, 2)}
            for k, p in enumerate(sorted(target.glob("kf_*.jpg")))]  # fmt: skip


def camera_path(scan: Scan) -> list[dict]:
    """The camera's path, every ``PATH_EVERY`` frames: time, ARKit position, and on the map."""
    headings = heading(scan)
    out = []
    for i in range(0, len(scan.t), PATH_EVERY):
        point = {"t": round(float(scan.t[i]), 3),
                 "xyz": [round(float(v), 3) for v in scan.position[i]]}  # fmt: skip
        if scan.geo is not None:
            point["lat"], point["lon"] = round(float(scan.geo[i, 0]), 7), round(float(scan.geo[i, 1]), 7)
            point["heading_deg"] = round(float(headings[i]), 1)
        out.append(point)
    return out


def store(scan: Scan, folder: Path) -> str:
    """The video copied into the moment's folder; returns its file name."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "video.mp4"
    if not target.exists() or target.stat().st_size != scan.video.stat().st_size:
        shutil.copyfile(scan.video, target)
    return target.name

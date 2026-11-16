"""The frames things are looked for in: a LiDAR scan's, with pose and measured depth, or a plain
video's, a keyframe per shot.

A scan (Stray Scanner: ``rgb.mp4``, ``odometry.csv``, ``depth/``, ``confidence/``) is read with
``ingest.splat.dataset``'s own functions, so poses and depth mean here what they mean for the
splats. Frames are kept as stored (a phone held upright films sideways); ``Frame.turn`` says how far
to turn one for a detector, and ``upright`` / ``box_back`` do the turning of the picture and of the
boxes found in it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np

from ..moments.media import executable
from ..splat import dataset
from .lift import LIDAR_M, Frame

SCAN_WIDTH = 960  # frames are decoded this wide: enough for a detector, a quarter of the pixels
KEY_EVERY_S = 2.0
SCENE = 0.25  # ffmpeg's scene score above which a new shot begins
PHONE_FOV_DEG = 69.0  # a phone's main camera across its long side


def upright(image: np.ndarray, turn: int) -> np.ndarray:
    return np.ascontiguousarray(np.rot90(image, {0: 0, 90: -1, 180: 2, 270: 1}[turn]))


def box_back(
    box: tuple[float, float, float, float], turn: int, width: int, height: int
) -> tuple[float, float, float, float]:
    """A box found in the upright picture, in the stored frame's pixels (``width`` x ``height``)."""
    x0, y0, x1, y1 = box
    if turn == 90:  # upright (x', y') = (height - y, x)
        xs, ys = (y0, y1), (height - x0, height - x1)
    elif turn == 270:  # upright (x', y') = (y, width - x)
        xs, ys = (width - y0, width - y1), (x0, x1)
    elif turn == 180:
        xs, ys = (width - x0, width - x1), (height - y0, height - y1)
    else:
        return box
    return (min(xs), min(ys), max(xs), max(ys))


def _turn(c2w: np.ndarray) -> int:
    """Degrees clockwise to stand upright, from where the world's up falls in the camera's axes
    (as ``ingest.phone.stray.upright``, from the camera-to-world matrices)."""
    up = np.median(c2w[:, 1, :2], 0)  # the world's y in the camera's x (right) and y (up)
    if abs(up[0]) > abs(up[1]):
        return 90 if up[0] < 0 else 270
    return 0 if up[1] >= 0 else 180


def scan_frames(
    capture: Path, every_s: float = KEY_EVERY_S, width: int = SCAN_WIDTH, limit: int | None = None
) -> Iterator[Frame]:
    """A frame every ``every_s`` seconds of a Stray Scanner capture, among those with a depth map."""
    video = capture / "rgb.mp4"
    full_w, full_h, _ = dataset._video_size(video)
    odo = dataset.read_odometry(capture, full_w, full_h)
    depth_of = {int(p.stem): p for p in (capture / "depth").glob("*.png")}
    rows, last = [], -1e9
    for i in range(len(odo.frame)):
        if int(odo.frame[i]) in depth_of and odo.t[i] - last >= every_s:
            rows.append(i)
            last = odo.t[i]
    rows = rows[:limit] if limit else rows
    height = round(width * full_h / full_w)
    picks = odo.frame[rows]
    images, found = dataset.frames_at(video, picks, (width, height))
    depth = dataset.read_maps([depth_of[int(f)] for f in picks], "gray16le", "<u2").astype(np.float32) / 1000.0
    conf_dir = capture / "confidence"
    good = None
    if conf_dir.is_dir() and all((conf_dir / f"{int(f):06d}.png").exists() for f in picks):
        good = dataset.read_maps([conf_dir / f"{int(f):06d}.png" for f in picks], "gray", "u1") >= 1
    turn = _turn(odo.c2w[rows])
    scale = width / full_w
    for k, i in enumerate(rows):
        if not found[k]:
            continue
        K = tuple(float(v) * scale for v in odo.K[i])
        yield Frame(int(odo.frame[i]), float(odo.t[i]), images[k], K, depth[k], None if good is None else good[k],
                    "lidar", odo.c2w[i], turn)  # fmt: skip


def shots(video: Path, scene: float = SCENE, shortest_s: float = 0.4) -> list[tuple[float, float]]:
    """The video's shots as (start, end) seconds, cut where ffmpeg's scene score jumps."""
    out = subprocess.run(
        [executable("ffmpeg"), "-nostdin", "-v", "info", "-i", str(video), "-vf", f"select='gt(scene,{scene})',showinfo",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=3600,
    ).stderr  # fmt: skip
    cuts = sorted({float(part.split("pts_time:")[1].split()[0]) for part in out.splitlines() if "pts_time:" in part})
    duration = float(subprocess.run(
        [executable("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout.strip())  # fmt: skip
    edges = [0.0, *cuts, duration]
    spans = [(a, b) for a, b in zip(edges[:-1], edges[1:], strict=True) if b - a >= shortest_s]
    return spans or [(0.0, duration)]


def frame_at(video: Path, t: float, width: int = SCAN_WIDTH) -> np.ndarray | None:
    """One frame of a video at ``t`` seconds, RGB, ``width`` wide (its rotation tag applied by ffmpeg)."""
    info = subprocess.run(
        [executable("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:stream_side_data=rotation",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout.replace("\n", ",").split(",")  # fmt: skip
    w, h = int(info[0]), int(info[1])
    if len(info) > 2 and info[2].strip().lstrip("-") in ("90", "270"):
        w, h = h, w
    height = round(width * h / w / 2) * 2
    raw = subprocess.run(
        [executable("ffmpeg"), "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={width}:{height}:flags=area,format=rgb24", "-f", "rawvideo", "pipe:1"],
        capture_output=True, timeout=300,
    ).stdout  # fmt: skip
    if len(raw) != width * height * 3:
        return None
    return np.frombuffer(raw, np.uint8).reshape(height, width, 3)


def sharpness(image: np.ndarray) -> float:
    grey = image.mean(2)
    lap = grey[1:-1, 1:-1] * 4 - grey[:-2, 1:-1] - grey[2:, 1:-1] - grey[1:-1, :-2] - grey[1:-1, 2:]
    return float(lap.var())


def video_frames(
    video: Path,
    depth_of: Callable[[np.ndarray], tuple[np.ndarray, str]],
    width: int = SCAN_WIDTH,
    fov_deg: float = PHONE_FOV_DEG,
) -> Iterator[tuple[int, tuple[float, float], Frame]]:
    """(shot number, (start, end), keyframe) for each shot: the sharpest of three looks into it,
    with ``depth_of(image)`` -> (depth in metres, kind) from a model. No pose: each shot's things
    are placed in its own camera's frame."""
    for n, (a, b) in enumerate(shots(video)):
        looks = [(t, frame_at(video, t, width)) for t in (a + (b - a) * f for f in (0.25, 0.5, 0.75))]
        looks = [(t, image) for t, image in looks if image is not None]
        if not looks:
            continue
        t, image = max(looks, key=lambda look: sharpness(look[1]))
        H, W = image.shape[:2]
        f = 0.5 * max(W, H) / np.tan(np.radians(fov_deg) / 2)
        depth, kind = depth_of(image)
        yield n, (a, b), Frame(n, t, image, (f, f, W / 2, H / 2), depth, None, kind)


__all__ = ["LIDAR_M", "box_back", "frame_at", "scan_frames", "shots", "upright", "video_frames"]

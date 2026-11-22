"""The frames things are looked for in: a LiDAR scan's, with pose and measured depth, or a plain
video's, shot by shot.

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
LOOK_EVERY_S = 1.0  # with camera poses, a shot is looked at this often ...
MAX_LOOKS = 24  # ... and at most this many times (a longer shot is looked at farther apart)
FLIP = np.diag([1.0, -1.0, -1.0, 1.0])  # OpenCV axes (y down, z forward) to OpenGL (y up, z back), and back


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


def recorded(video: Path) -> dict | None:
    """When a recording was made, from the video's own creation time (taken as its start) and
    length: ``{"t0": ISO time, "duration_s"}``, or None when the file does not say. Kept with the
    items so that deleting a span of a day (ingest/forget.py) can tell whether they came from it."""
    from ..moments.media import probe

    try:
        info = probe(video)["format"]
        created = (info.get("tags") or {})["creation_time"]
        return {"t0": created.replace("Z", "+00:00"), "duration_s": round(float(info["duration"]), 2)}
    except (OSError, RuntimeError, KeyError, ValueError, subprocess.SubprocessError):
        return None


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


def _size(video: Path, width: int) -> tuple[int, int]:
    """(width, height) of the video's frames ``width`` wide, as ffmpeg hands them over (its rotation
    tag applied, the height even)."""
    info = subprocess.run(
        [executable("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:stream_side_data=rotation",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout.replace("\n", ",").split(",")  # fmt: skip
    w, h = int(info[0]), int(info[1])
    if len(info) > 2 and info[2].strip().lstrip("-") in ("90", "270"):
        w, h = h, w
    return width, round(width * h / w / 2) * 2


def looks(video: Path, start: float, every: float, count: int, size: tuple[int, int]) -> list[np.ndarray]:
    """Up to ``count`` frames of a video, the first at ``start`` seconds and one every ``every``
    after it, RGB at ``size``: one run of the decoder from the first to the last. (A seek for each
    decodes from the keyframe before it every time: 0.7 s a frame on a phone's video.)"""
    width, height = size
    raw = subprocess.run(
        [executable("ffmpeg"), "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-i", str(video),
         "-vf", f"select='gte(t\\,{every:.3f}*selected_n)',scale={width}:{height}:flags=area,format=rgb24",
         "-fps_mode", "passthrough", "-frames:v", str(count), "-f", "rawvideo", "pipe:1"],
        capture_output=True, timeout=600,
    ).stdout  # fmt: skip
    whole = len(raw) // (width * height * 3)
    return list(np.frombuffer(raw, np.uint8)[: whole * width * height * 3].reshape(whole, height, width, 3))


def sharpness(image: np.ndarray) -> float:
    grey = image.mean(2)
    lap = grey[1:-1, 1:-1] * 4 - grey[:-2, 1:-1] - grey[2:, 1:-1] - grey[1:-1, :-2] - grey[1:-1, 2:]
    return float(lap.var())


def posed(first: int, looks: list[tuple[float, np.ndarray]], poses: dict[str, np.ndarray], key: int,
           metres: np.ndarray, kind: str) -> list[Frame]:  # fmt: skip
    """One shot's looks as frames in the key look's camera, in metres, the key first. The pose
    model's unit of length is its own: the key look's depth in metres, over the model's depth of the
    same pixels, is what one unit measures."""
    w2c = np.concatenate([poses["w2c"], np.tile([[[0.0, 0.0, 0.0, 1.0]]], (len(looks), 1, 1))], 1)
    h, w = poses["depth"].shape[1:]
    H, W = metres.shape
    rows, cols = ((np.arange(h) + 0.5) * H / h).astype(int), ((np.arange(w) + 0.5) * W / w).astype(int)
    own = poses["depth"][key]
    ok = np.isfinite(own) & (own > 0)
    unit = float(np.median(metres[rows][:, cols][ok] / own[ok]))
    frames = []
    for k, (t, image) in enumerate(looks):
        c2w = FLIP @ w2c[key] @ np.linalg.inv(w2c[k]) @ FLIP  # this look's camera to the key's
        c2w[:3, 3] *= unit
        fx, fy, cx, cy = (float(v) for v in poses["K"][k][[0, 1, 0, 1], [0, 1, 2, 2]])
        frames.append(Frame(first + k, t, image, (fx * W / w, fy * H / h, cx * W / w, cy * H / h),
                            poses["depth"][k] * unit, None, kind, c2w))  # fmt: skip
    return [frames[key], *frames[:key], *frames[key + 1 :]]


def video_frames(
    video: Path,
    depth_of: Callable[[np.ndarray], tuple[np.ndarray, str]],
    poses_of: Callable[[np.ndarray], dict[str, np.ndarray]] | None = None,
    width: int = SCAN_WIDTH,
    fov_deg: float = PHONE_FOV_DEG,
) -> Iterator[tuple[int, tuple[float, float], list[Frame]]]:
    """(shot number, (start, end), frames) for each shot, its keyframe first: the sharpest of the
    looks into it, with ``depth_of(image)`` -> (depth in metres, kind) from a model.

    Without ``poses_of`` a shot gets three looks and only the keyframe is kept, with no pose and a
    phone's field of view: its things are placed in its camera. With it (``models.Poses``) a shot
    is looked at every ``LOOK_EVERY_S`` and every look is kept, placed in the keyframe's camera
    with the focal length and depth the pose model gives. Frames are numbered through the video
    in the order kept."""
    size = _size(video, width)
    first = 0
    for n, (a, b) in enumerate(shots(video)):
        count = 3 if poses_of is None else int(np.clip(round((b - a) / LOOK_EVERY_S), 2, MAX_LOOKS))
        every = (b - a) / (count + 1)
        seen = [(a + every * (k + 1), image) for k, image in enumerate(looks(video, a + every, every, count, size))]
        if not seen:
            continue
        key = max(range(len(seen)), key=lambda k: sharpness(seen[k][1]))
        t, image = seen[key]
        metres, kind = depth_of(image)
        if poses_of is None:
            f = 0.5 * max(size) / np.tan(np.radians(fov_deg) / 2)
            frames = [Frame(first, t, image, (f, f, size[0] / 2, size[1] / 2), metres, None, kind)]
        else:
            frames = posed(first, seen, poses_of(np.stack([image for _, image in seen])), key, metres, kind)
        first += len(frames)
        yield n, (a, b), frames


__all__ = ["LIDAR_M", "box_back", "looks", "posed", "scan_frames", "shots", "upright", "video_frames"]

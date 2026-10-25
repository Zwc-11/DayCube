"""A LiDAR scan (Stray Scanner layout) as a Gaussian-splat training set: the sharpest frames of the
video, each with ARKit's pose and the camera's intrinsics, in nerfstudio's ``transforms.json``, and
a starting point cloud fused from the LiDAR depth.

No structure from motion is run: ARKit already knows where the camera was for every frame (metres,
gravity aligned, y up). Stray Scanner writes those poses in the OpenCV camera convention (x right,
y down, looking along +z: on 2026-10-24 the +z axis pointed along the walk, cosine 0.93, and -z
against it); nerfstudio and Brush read OpenGL's (y up, looking along -z), so the camera's y and z
axes are flipped on reading. The video's sensor is landscape, so a phone held upright gives
sideways frames; poses and intrinsics are in that same sensor frame, so nothing is turned here.

**Frames.** The video is decoded once at 480 px to score sharpness (variance of the Laplacian:
walking blurs frames), and the sharpest frame of every window of ``len / target`` frames is kept.
A second pass writes those frames at the training size and, from the same decode, a 256 x 192 copy
the size of the depth maps, for colouring the points.

**Points.** Each kept frame's depth map (256 x 192, millimetres) is back-projected where ARKit's
confidence is medium or high and the depth is in the LiDAR's reliable range (5.5 m), moved into the world by
the frame's pose, coloured from the frame, and merged on a voxel grid (the grid coarsens until at
most ``max_points`` remain). Beyond that range ARKit's depth is its estimate rather than a LiDAR
return (to 15 m, any confidence): a quarter of those pixels, on a 10 cm grid, seed the trees and
walls the LiDAR cannot reach, so Brush does not have to grow them from nothing. Brush starts from
these points instead of random splats, which is what lets a forward walk, with little parallax,
train.

**Check.** Points seen from one frame are projected into the next kept frame with that frame's
pose and intrinsics; their colours must match the frame's far better than at a pose turned by
5 degrees (``check``), or the conventions are wrong and training would waste the GPU.

ffmpeg does all the decoding (video, 16-bit depth PNGs, confidence), so nothing beyond numpy is
needed.
"""

from __future__ import annotations

import csv
import json
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..moments.media import executable

DEPTH_W, DEPTH_H = 256, 192
SCORE_W = 480


@dataclass
class Odometry:
    frame: np.ndarray  # (n,) frame numbers, as the depth files are named
    t: np.ndarray  # (n,) seconds from the first frame
    c2w: np.ndarray  # (n, 4, 4) camera to world, OpenGL convention (converted from Stray's OpenCV), metres
    K: np.ndarray  # (n, 4) fx, fy, cx, cy at the video's resolution


def rotation(q: np.ndarray) -> np.ndarray:
    """Rotation matrices from quaternions (n, 4) as x, y, z, w."""
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    x, y, z, w = q.T
    return np.stack(
        [
            np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
            np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
            np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1),
        ],
        1,
    )


def read_odometry(capture: Path, width: int, height: int) -> Odometry:
    """``odometry.csv``: a row per video frame (time, frame, position, orientation and, from newer
    Stray Scanner versions, the frame's own intrinsics; else ``camera_matrix.csv``'s)."""
    with (capture / "odometry.csv").open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = [h.strip() for h in next(reader)]
        rows = [r for r in reader if len(r) >= 9]
    col = {name: header.index(name) for name in header}
    num = lambda r, k: float(r[col[k]]) if k in col and r[col[k]].strip() else np.nan  # noqa: E731
    data = np.array([[num(r, k) for k in ("timestamp", "x", "y", "z", "qx", "qy", "qz", "qw", "fx", "fy", "cx", "cy")]
                     for r in rows])  # fmt: skip
    frame = np.array([int(r[col["frame"]]) for r in rows])
    c2w = np.tile(np.eye(4), (len(rows), 1, 1))
    c2w[:, :3, :3] = rotation(data[:, 4:8])
    c2w[:, :3, 1:3] *= -1  # OpenCV camera axes (y down, z forward) to OpenGL's (y up, z back)
    c2w[:, :3, 3] = data[:, 1:4]
    K = data[:, 8:12]
    if not np.isfinite(K).all():
        m = np.loadtxt(capture / "camera_matrix.csv", delimiter=",")
        K = np.tile([m[0, 0], m[1, 1], m[0, 2], m[1, 2]], (len(rows), 1))
    # Intrinsics are for the video's resolution; a capture exported smaller says so by its size.
    sx, sy = width / (2 * np.median(K[:, 2])), height / (2 * np.median(K[:, 3]))
    if abs(sx - 1) > 0.05 or abs(sy - 1) > 0.05:
        K = K * [sx, sy, sx, sy]
    return Odometry(frame, data[:, 0] - data[0, 0], c2w, K)


def _video_size(video: Path) -> tuple[int, int, int]:
    out = subprocess.run(
        [executable("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=width,height,nb_read_packets", "-of", "csv=p=0", str(video)],
        check=True, capture_output=True, text=True, timeout=600,
    ).stdout.strip().split(",")  # fmt: skip
    return int(out[0]), int(out[1]), int(out[2])


def sharpness(video: Path, width: int, height: int) -> np.ndarray:
    """Variance of the Laplacian of every frame, at ``SCORE_W`` px across."""
    h = round(height * SCORE_W / width / 2) * 2
    size = SCORE_W * h
    proc = subprocess.Popen(
        [executable("ffmpeg"), "-nostdin", "-v", "error", "-i", str(video), "-vf", f"scale={SCORE_W}:{h},format=gray",
         "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE,
    )  # fmt: skip
    scores = []
    assert proc.stdout is not None
    while chunk := proc.stdout.read(size):
        if len(chunk) < size:
            break
        g = np.frombuffer(chunk, np.uint8).reshape(h, SCORE_W).astype(np.float32)
        lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
        scores.append(float(lap.var()))
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg could not decode {video.name}")
    return np.array(scores)


def choose(scores: np.ndarray, target: int, usable: np.ndarray) -> np.ndarray:
    """The sharpest frame of each of ``target`` equal windows of the usable frames."""
    frames = np.nonzero(usable)[0]
    edges = np.linspace(0, len(frames), min(target, len(frames)) + 1).astype(int)
    picked = [int(frames[a + np.argmax(scores[frames[a:b]])]) for a, b in zip(edges[:-1], edges[1:], strict=True) if b > a]
    return np.array(picked, dtype=int)


def extract(video: Path, picks: np.ndarray, images: Path, size: tuple[int, int]) -> np.ndarray:
    """Write the picked frames as JPEGs at ``size`` (``images/00000.jpg``...) and return them at
    the depth maps' size, RGB, for colouring the points.

    One ffmpeg decodes every frame in order (the same frames, counted the same way, as
    ``sharpness``) and the picked ones are passed to a second that writes them. No frame list is
    given to ffmpeg: a ``select`` expression of 420 terms is refused by some builds ("Cannot
    allocate memory", FFmpeg on Windows, 2026-10-25)."""
    images.mkdir(parents=True, exist_ok=True)
    width, height = size
    frame_bytes = width * height * 3
    wanted = {int(i) for i in picks}
    last = max(wanted)
    decode = subprocess.Popen(
        [executable("ffmpeg"), "-nostdin", "-v", "error", "-i", str(video),
         "-vf", f"scale={width}:{height}:flags=lanczos,format=rgb24", "-fps_mode", "passthrough",
         "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE,
    )  # fmt: skip
    encode = subprocess.Popen(
        [executable("ffmpeg"), "-nostdin", "-v", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", "1", "-i", "pipe:0",
         "-filter_complex", f"[0:v]split=2[big][b];[b]scale={DEPTH_W}:{DEPTH_H}:flags=area,format=rgb24[small]",
         "-map", "[big]", "-fps_mode", "passthrough", "-q:v", "2", "-start_number", "0", str(images / "%05d.jpg"),
         "-map", "[small]", "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )  # fmt: skip
    assert decode.stdout is not None and encode.stdin is not None and encode.stdout is not None
    out: list[bytes] = []
    reader = threading.Thread(target=lambda: out.append(encode.stdout.read()), daemon=True)  # type: ignore[union-attr]
    reader.start()
    sent = index = 0
    try:
        while index <= last and len(frame := decode.stdout.read(frame_bytes)) == frame_bytes:
            if index in wanted:
                encode.stdin.write(frame)
                sent += 1
            index += 1
    finally:
        encode.stdin.close()
        if decode.poll() is None:
            decode.kill()  # the frames after the last pick are not needed
        decode.wait()
        decode.stdout.close()
        reader.join()
    if encode.wait() != 0 or sent != len(wanted):
        raise RuntimeError(f"ffmpeg could not extract frames from {video.name} ({sent} of {len(wanted)} written)")
    small = np.frombuffer(out[0] if out else b"", np.uint8).reshape(-1, DEPTH_H, DEPTH_W, 3)
    if len(small) != len(picks):
        raise RuntimeError(f"expected {len(picks)} frames from {video.name}, got {len(small)}")
    return small


def frames_at(video: Path, picks: np.ndarray, size: tuple[int, int] = (DEPTH_W, DEPTH_H)) -> tuple[np.ndarray, np.ndarray]:
    """The picked frames (decoded frame numbers, counted as in ``sharpness``) at ``size``, RGB, and
    whether each was decoded (a video can decode a frame short of its packet count)."""
    width, height = size
    frame_bytes = width * height * 3
    wanted = {int(i): k for k, i in enumerate(picks)}
    out = np.zeros((len(picks), height, width, 3), np.uint8)
    decode = subprocess.Popen(
        [executable("ffmpeg"), "-nostdin", "-v", "error", "-i", str(video),
         "-vf", f"scale={width}:{height}:flags=area,format=rgb24", "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE,
    )  # fmt: skip
    assert decode.stdout is not None
    found = np.zeros(len(picks), bool)
    index = 0
    last = max(wanted) if wanted else -1
    try:
        while index <= last and len(frame := decode.stdout.read(frame_bytes)) == frame_bytes:
            if index in wanted:
                out[wanted[index]] = np.frombuffer(frame, np.uint8).reshape(height, width, 3)
                found[wanted[index]] = True
            index += 1
    finally:
        if decode.poll() is None:
            decode.kill()
        decode.wait()
        decode.stdout.close()
    if found.sum() < len(picks) - 1:
        raise RuntimeError(f"{video.name}: {int(found.sum())} of {len(picks)} frames decoded")
    return out, found


def read_maps(paths: list[Path], pix_fmt: str, dtype: str) -> np.ndarray:
    """PNGs (depth: 16-bit, confidence: 8-bit) decoded by ffmpeg's concat demuxer, in order."""
    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "list.txt"
        listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in paths), encoding="utf-8")
        raw = subprocess.run(
            [executable("ffmpeg"), "-nostdin", "-v", "fatal", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-fps_mode", "passthrough", "-pix_fmt", pix_fmt, "-f", "rawvideo", "pipe:1"],
            check=True, capture_output=True, timeout=1800,
        ).stdout  # fmt: skip
    maps = np.frombuffer(raw, dtype).reshape(-1, DEPTH_H, DEPTH_W)
    if len(maps) != len(paths):
        raise RuntimeError(f"expected {len(paths)} maps, decoded {len(maps)}")
    return maps


def unproject(u: np.ndarray, v: np.ndarray, z: np.ndarray, K: np.ndarray, c2w: np.ndarray, s: float) -> np.ndarray:
    """World points of depth-map pixels (u, v) at depth z; K at the video's resolution, ``s`` the
    depth map's width over the video's."""
    fx, fy, cx, cy = K
    x = ((u + 0.5) / s - 0.5 - cx) / fx * z
    y = ((v + 0.5) / s - 0.5 - cy) / fy * z
    cam = np.stack([x, -y, -z], 1)  # OpenCV (y down, z forward) to OpenGL (y up, z back)
    return cam @ c2w[:3, :3].T + c2w[:3, 3]


def back_project(depth_m: np.ndarray, K: np.ndarray, c2w: np.ndarray, mask: np.ndarray, s: float) -> np.ndarray:
    """World points (m, 3) of one depth map where ``mask``, in ``np.nonzero(mask)`` order."""
    v, u = np.nonzero(mask)
    return unproject(u, v, depth_m[v, u], K, c2w, s)


def voxel_merge(xyz: np.ndarray, rgb: np.ndarray, size: float, cap: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Average points and colours per voxel; the voxel grows until at most ``cap`` remain."""
    while True:
        keys = np.floor((xyz - xyz.min(0)) / size).astype(np.int64)
        span = keys.max(0) + 1
        flat = (keys[:, 0] * span[1] + keys[:, 1]) * span[2] + keys[:, 2]
        uniq, inverse, counts = np.unique(flat, return_inverse=True, return_counts=True)
        if len(uniq) <= cap:
            break
        size *= float(np.sqrt(len(uniq) / cap)) * 1.05
    out_xyz = np.stack([np.bincount(inverse, xyz[:, k], len(uniq)) for k in range(3)], 1) / counts[:, None]
    out_rgb = np.stack([np.bincount(inverse, rgb[:, k], len(uniq)) for k in range(3)], 1) / counts[:, None]
    return out_xyz.astype(np.float32), np.clip(np.round(out_rgb), 0, 255).astype(np.uint8), size


def write_ply(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    """A binary point cloud: x, y, z as floats, red, green, blue as bytes."""
    record = np.zeros(len(xyz), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("red", "u1"), ("green", "u1"), ("blue", "u1")])
    record["x"], record["y"], record["z"] = xyz.T
    record["red"], record["green"], record["blue"] = rgb.T
    header = ("ply\nformat binary_little_endian 1.0\n"
              f"element vertex {len(xyz)}\n"
              "property float x\nproperty float y\nproperty float z\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")  # fmt: skip
    with path.open("wb") as stream:
        stream.write(header.encode("ascii"))
        stream.write(record.tobytes())


def project(points: np.ndarray, K: np.ndarray, c2w: np.ndarray, s: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pixel coordinates at the depth maps' size of world points seen by a camera, and whether
    each is in front of it and inside the frame."""
    fx, fy, cx, cy = K
    w2c = np.linalg.inv(c2w)
    cam = points @ w2c[:3, :3].T + w2c[:3, 3]
    z = -cam[:, 2]
    zs = np.where(z > 1e-3, z, 1.0)
    u = ((cam[:, 0] / zs) * fx + cx + 0.5) * s - 0.5
    v = ((-cam[:, 1] / zs) * fy + cy + 0.5) * s - 0.5
    inside = (z > 0.05) & (u >= 0) & (u <= DEPTH_W - 1) & (v >= 0) & (v <= DEPTH_H - 1)
    return u, v, inside


def sky_shell(
    positions: np.ndarray,
    K: np.ndarray,
    c2w: np.ndarray,
    images: np.ndarray,
    s: float,
    *,
    count: int = 150_000,
    lowest_deg: float = -10.0,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Points on a sphere far around the walk (three times its reach from its middle), above
    ``lowest_deg`` of elevation, each coloured from the kept frame that looks most directly at it;
    points no frame sees are left out.

    Brush renders on black and starts only from these points: with nothing past the LiDAR's 15 m,
    the sky between the trees starts black and is learned as floaters in front of them, which the
    held-out frames do not see (2026-10-24: 14.6 dB PSNR, SSIM 0.21 at 9,000 steps)."""
    centre = positions.mean(0)
    radius = max(100.0, 3.0 * float(np.linalg.norm(positions - centre, axis=1).max()))
    floor = np.sin(np.radians(lowest_deg))
    total = int(count * 2 / (1 - floor))  # an even spread over the whole sphere, the cap above ``floor`` kept
    i = np.arange(total) + 0.5
    y = 1 - 2 * i / total
    ring = np.sqrt(np.clip(1 - y * y, 0, None))
    phi = i * np.pi * (3 - np.sqrt(5))
    keep = y >= floor
    points = centre + radius * np.stack([ring * np.cos(phi), y, ring * np.sin(phi)], 1)[keep]
    best = np.full(len(points), -np.inf)
    colours = np.zeros((len(points), 3), np.uint8)
    for k in range(len(c2w)):
        u, v, inside = project(points, K[k], c2w[k], s)
        if not inside.any():
            continue
        ahead = -c2w[k][:3, 2]
        towards = points[inside] - c2w[k][:3, 3]
        aligned = towards @ ahead / np.linalg.norm(towards, axis=1)
        idx = np.nonzero(inside)[0]
        better = aligned > best[idx]
        idx = idx[better]
        best[idx] = aligned[better]
        colours[idx] = images[k][np.round(v[idx]).astype(int), np.round(u[idx]).astype(int)]
    seen = np.isfinite(best)
    return points[seen].astype(np.float32), colours[seen], radius


def depth_error(points: np.ndarray, K: np.ndarray, c2w: np.ndarray, depth_m: np.ndarray, good: np.ndarray, s: float) -> float | None:
    """Median relative difference between the depth world points should have in a frame and the
    depth its LiDAR measured there (``good``: pixels to trust). None when too few land in it."""
    u, v, inside = project(points, K, c2w, s)
    w2c = np.linalg.inv(c2w)
    z = -(points[inside] @ w2c[:3, :3].T + w2c[:3, 3])[:, 2]
    ui, vi = np.round(u[inside]).astype(int), np.round(v[inside]).astype(int)
    measured = depth_m[vi, ui]
    keep = good[vi, ui]
    if keep.sum() < 50:
        return None
    return float(np.median(np.abs(z[keep] - measured[keep]) / measured[keep]))


def turned(c2w: np.ndarray, degrees: float) -> np.ndarray:
    """The pose turned about the world's up axis by ``degrees``."""
    a = np.radians(degrees)
    R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    out = c2w.copy()
    out[:3, :3] = R @ c2w[:3, :3]
    return out


def tilted(c2w: np.ndarray, degrees: float) -> np.ndarray:
    """The pose pitched about the camera's own x axis by ``degrees``."""
    a = np.radians(degrees)
    out = c2w.copy()
    out[:3, :3] = c2w[:3, :3] @ np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    return out


MIRROR = np.diag([1.0, -1.0, -1.0, 1.0])


def colour_error(u: np.ndarray, v: np.ndarray, z: np.ndarray, colours: np.ndarray, K_from: np.ndarray, c2w_from: np.ndarray,
                 image: np.ndarray, K_to: np.ndarray, c2w_to: np.ndarray, s: float) -> float | None:
    """Mean colour difference of depth pixels of one frame carried into another (both poses as
    given), against that frame's colours there."""
    points = unproject(u, v, z, K_from, c2w_from, s)
    pu, pv, inside = project(points, K_to, c2w_to, s)
    if inside.sum() < 50:
        return None
    got = image[np.round(pv[inside]).astype(int), np.round(pu[inside]).astype(int)].astype(np.float32)
    return float(np.abs(got - colours[inside].astype(np.float32)).mean())


def check(samples: list[dict], s: float) -> dict:
    """Do poses and intrinsics agree with the LiDAR and the video? Depth pixels of one kept frame
    are carried into the next.

    - Geometry: the depth they should have there against the depth measured there, at the
      recorded pose and at two wrong ones (a second later; tilted 5 degrees, since a flat floor
      looks the same a second later). On 2026-10-24: 0.9% against 9.4% a second later.
    - Colour: the frame's colours there against theirs, with the poses read in this convention
      and in the mirrored one (OpenCV against OpenGL axes). Geometry alone cannot tell those apart
      over flat ground, walking forward and walking backward fit it alike; the picture can. On
      2026-10-24: 25.3 against 39.3 (8-bit levels).
    """
    now, later, tilt, colour, mirrored = [], [], [], [], []
    for x in samples:
        err = lambda pose: depth_error(x["points"], x["K_to"], pose, x["depth_to"], x["good_to"], s)  # noqa: E731,B023
        a, b = err(x["c2w_to"]), err(x["c2w_later"])
        # Pitched, a vertical wall keeps its depths; turned, a floor does: the worse of the two.
        c = [e for e in (err(tilted(x["c2w_to"], 5.0)), err(turned(x["c2w_to"], 5.0))) if e is not None]
        if a is not None:
            now.append(a)
        if b is not None:
            later.append(b)
        if c:
            tilt.append(max(c))
        c = colour_error(x["u"], x["v"], x["z"], x["colours"], x["K_from"], x["c2w_from"], x["image_to"], x["K_to"], x["c2w_to"], s)
        m = colour_error(x["u"], x["v"], x["z"], x["colours"], x["K_from"], x["c2w_from"] @ MIRROR, x["image_to"], x["K_to"],
                         x["c2w_to"] @ MIRROR, s)  # fmt: skip
        if c is not None and m is not None:
            colour.append(c)
            mirrored.append(m)
    if len(now) < 5 or len(colour) < 5:
        return {"pairs": len(now), "ok": False, "reason": "too few frames see the same points"}
    error = float(np.median(now))
    base = max(float(np.median(later)) if later else 0.0, float(np.median(tilt)) if tilt else 0.0) or float("inf")
    c, m = float(np.median(colour)), float(np.median(mirrored))
    # The wrong-pose error is reported, not required: a corridor seen from a level camera keeps its
    # depths under small turns (on 2026-10-24 it was 7 times the true pose's; in a synthetic hall,
    # barely more). The colour test is what tells a mirrored convention, the depth bound what
    # tells wrong intrinsics or depth from another frame.
    return {"pairs": len(now), "depth_error": round(error, 4), "wrong_pose_error": round(base, 4),
            "colour_error": round(c, 2), "mirrored_colour_error": round(m, 2),
            "ok": error < 0.03 and c < 0.85 * m}  # fmt: skip


def build(
    capture: Path,
    out: Path,
    *,
    target: int = 1260,
    long_side: int = 1440,
    max_depth_m: float = 5.5,
    min_confidence: int = 1,
    voxel_m: float = 0.02,
    max_points: int = 1_200_000,
    far_depth_m: float = 15.0,
    far_voxel_m: float = 0.10,
    far_points: int = 300_000,
    sky_points: int = 150_000,
    video: Path | None = None,
    say: Callable[[str], None] = print,
) -> dict:
    """Write ``out/transforms.json``, ``out/images/``, ``out/init.ply`` (Brush's starting points) and
    ``out/lidar.ply`` (the LiDAR's returns only) from a capture folder; returns what was made, with
    the pose check. ``video``: the capture's video when it is kept elsewhere (a moment's
    ``video.mp4``)."""
    video = video or capture / "rgb.mp4"
    width, height, count = _video_size(video)
    odo = read_odometry(capture, width, height)
    n = min(count, len(odo.frame))
    depth_of = {int(p.stem): p for p in (capture / "depth").glob("*.png")} if (capture / "depth").is_dir() else {}
    conf_dir = capture / "confidence"
    say(f"Scoring {n} frames for sharpness")
    scores = sharpness(video, width, height)[:n]
    n = len(scores)  # a video can decode a frame short of its packet count
    # ARKit's first poses come before tracking settles (on 2026-10-24 the first looked 34 degrees
    # down, a second later 8): frames from the first half second are not trained on.
    usable = np.isfinite(odo.c2w[:n, 0, 0]) & (odo.t[:n] >= 0.5)
    picks = choose(scores, target, usable)
    scale = min(1.0, long_side / max(width, height))  # never larger than the video
    size = (round(width * scale / 2) * 2, round(height * scale / 2) * 2)
    say(f"Writing {len(picks)} frames at {size[0]}x{size[1]}")
    small = extract(video, picks, out / "images", size)

    frames = []
    for k, i in enumerate(picks):
        fx, fy, cx, cy = odo.K[i]
        frames.append({
            "file_path": f"images/{k:05d}.jpg",
            "transform_matrix": [[round(float(v), 7) for v in row] for row in odo.c2w[i]],
            "fl_x": fx * size[0] / width, "fl_y": fy * size[1] / height,
            "cx": (cx + 0.5) * size[0] / width - 0.5, "cy": (cy + 0.5) * size[1] / height - 0.5,
            "w": size[0], "h": size[1],
            "t": round(float(odo.t[i]), 4), "frame": int(odo.frame[i]),
        })  # fmt: skip

    with_depth = [k for k, i in enumerate(picks) if int(odo.frame[i]) in depth_of]
    points: list[np.ndarray] = []
    colours: list[np.ndarray] = []
    far: list[np.ndarray] = []
    far_colours: list[np.ndarray] = []
    samples = []
    init = None
    if with_depth:
        say(f"Fusing LiDAR depth from {len(with_depth)} frames")
        depth = read_maps([depth_of[int(odo.frame[picks[k]])] for k in with_depth], "gray16le", "<u2")
        conf_paths = [conf_dir / f"{int(odo.frame[picks[k]]):06d}.png" for k in with_depth]
        conf = read_maps(conf_paths, "gray", "u1") if all(p.is_file() for p in conf_paths) else None
        rng = np.random.default_rng(0)
        # Frames about 0.9 s apart are compared: closer, a wrong pose barely moves anything.
        duration = float(odo.t[picks[-1]] - odo.t[picks[0]]) or 1.0
        gap = max(1, round(0.9 * len(with_depth) / duration))
        for j, k in enumerate(with_depth):
            i = picks[k]
            d = depth[j].astype(np.float32) / 1000.0
            mask = (d > 0.2) & (d < max_depth_m)
            if conf is not None:
                mask &= conf[j] >= min_confidence
            pts = back_project(d, odo.K[i], odo.c2w[i], mask, DEPTH_W / width)
            v, u = np.nonzero(mask)
            col = small[k][v, u]
            points.append(pts)
            colours.append(col)
            # ARKit marks most of what is past the LiDAR as low confidence: as seeds they only
            # need to be near, so any confidence will do.
            beyond = (d >= max_depth_m) & (d < far_depth_m) & (rng.random(d.shape) < 0.25)
            if beyond.any():
                fv, fu = np.nonzero(beyond)
                far.append(back_project(d, odo.K[i], odo.c2w[i], beyond, DEPTH_W / width))
                far_colours.append(small[k][fv, fu])
            if j + gap < len(with_depth) and len(pts) and j % 3 == 0:
                nxt = picks[with_depth[j + gap]]
                pick = np.sort(rng.choice(len(pts), min(3000, len(pts)), replace=False))
                d_next = depth[j + gap].astype(np.float32) / 1000.0
                good = (d_next > 0.2) & (d_next < max_depth_m) & (conf[j + gap] >= 2 if conf is not None else True)
                samples.append({
                    "points": pts[pick], "u": u[pick], "v": v[pick], "z": d[v[pick], u[pick]], "colours": col[pick],
                    "K_from": odo.K[i], "c2w_from": odo.c2w[i],
                    "K_to": odo.K[nxt], "c2w_to": odo.c2w[nxt], "c2w_later": odo.c2w[min(nxt + 30, n - 1)],
                    "depth_to": d_next, "good_to": good, "image_to": small[with_depth[j + gap]],
                })  # fmt: skip
        xyz = np.concatenate(points)
        rgb = np.concatenate(colours).astype(np.float64)
        merged, merged_rgb, used = voxel_merge(xyz, rgb, voxel_m, max_points)
        init = {"points": int(len(merged)), "from_points": int(len(xyz)), "voxel_m": round(used, 4), "far_points": 0}
        write_ply(out / "lidar.ply", merged, merged_rgb)  # the LiDAR's own returns, for the preview
        if far:
            fxyz, frgb, far_used = voxel_merge(np.concatenate(far), np.concatenate(far_colours).astype(np.float64), far_voxel_m, far_points)
            write_ply(out / "far.ply", fxyz, frgb)
            merged = np.concatenate([merged, fxyz])
            merged_rgb = np.concatenate([merged_rgb, frgb])
            init["far_points"] = int(len(fxyz))
            init["far_voxel_m"] = round(far_used, 4)
        if sky_points:
            sxyz, srgb, radius = sky_shell(odo.c2w[picks, :3, 3], odo.K[picks], odo.c2w[picks], small, DEPTH_W / width,
                                           count=sky_points)  # fmt: skip
            write_ply(out / "sky.ply", sxyz, srgb)
            merged = np.concatenate([merged, sxyz])
            merged_rgb = np.concatenate([merged_rgb, srgb])
            init["sky_points"] = int(len(sxyz))
            init["sky_radius_m"] = round(radius, 1)
        write_ply(out / "init.ply", merged, merged_rgb)
        say(f"Starting cloud: {init['points']:,} LiDAR points from {len(xyz):,} (voxel {used * 100:.1f} cm),"
            f" {init['far_points']:,} beyond its range and {init.get('sky_points', 0):,} on the sky"
            f" {init.get('sky_radius_m', 0):.0f} m out")

    transforms = {"camera_model": "OPENCV", "frames": frames}
    if init:
        transforms["ply_file_path"] = "init.ply"
    (out / "transforms.json").write_text(json.dumps(transforms, indent=1), encoding="utf-8")
    verdict = check(samples, DEPTH_W / width) if samples else {"pairs": 0, "ok": None}
    span = odo.c2w[picks, :3, 3]
    return {
        "frames": len(picks),
        "size": list(size),
        "video_frames": n,
        "sharpness": {"kept_median": round(float(np.median(scores[picks])), 1), "all_median": round(float(np.median(scores)), 1)},
        "path_m": round(float(np.linalg.norm(np.diff(odo.c2w[:n, :3, 3], axis=0), axis=1).sum()), 1),
        "extent_m": [round(float(v), 1) for v in span.max(0) - span.min(0)],
        "init": init,
        "check": verdict,
    }

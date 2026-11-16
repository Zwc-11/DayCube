"""A box in a frame carried into space: where the thing is and how large.

The place is the median of the 3D points behind the middle of the box, not the point behind its
centre: a box holds background at its edges, and a depth map has holes. With LiDAR the depth is
trusted to ``LIDAR_M`` (beyond it ARKit reports an estimate); a model's depth is used whole and
marked as a model's.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LIDAR_M = 5.5  # the iPhone LiDAR's reliable range (ingest.splat.dataset)
NEAR_M = 0.15
MIDDLE = 0.5  # the share of the box, about its centre, whose depth places it
MIN_PIXELS = 6


@dataclass(frozen=True)
class Frame:
    """One picture with what is needed to place things seen in it."""

    index: int  # the recording's frame number
    t: float  # seconds from the recording's start
    image: np.ndarray  # (H, W, 3) RGB
    K: tuple[float, float, float, float]  # fx, fy, cx, cy at the image's size
    depth: np.ndarray | None = None  # (h, w) metres along the camera's axis, any size with the image's shape
    good: np.ndarray | None = None  # (h, w) bool: depth to trust (LiDAR confidence, range); None: all of it
    depth_kind: str = "lidar"  # "lidar" | "model"
    c2w: np.ndarray | None = None  # (4, 4) camera to world, OpenGL axes (x right, y up, z back); None: camera frame
    turn: int = 0  # degrees clockwise the image must turn to stand upright (a phone held upright films sideways)


@dataclass(frozen=True)
class Place:
    position: np.ndarray  # (3,) world metres (camera metres when the frame has no pose)
    distance_m: float  # along the camera's axis
    size_m: tuple[float, float]  # width, height of the box at that distance
    depth: str  # "lidar" | "estimate" | "model"
    pixels: int  # depth pixels that placed it


def place(frame: Frame, box: tuple[float, float, float, float]) -> Place | None:
    """Where the thing in ``box`` (x0, y0, x1, y1 in image pixels) is, or None when the frame has no
    usable depth behind it."""
    if frame.depth is None:
        return None
    H, W = frame.image.shape[:2]
    h, w = frame.depth.shape
    sx, sy = w / W, h / H
    x0, y0, x1, y1 = box
    cx, cy, bw, bh = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    u0, u1 = int(np.floor((cx - MIDDLE * bw / 2) * sx)), int(np.ceil((cx + MIDDLE * bw / 2) * sx))
    v0, v1 = int(np.floor((cy - MIDDLE * bh / 2) * sy)), int(np.ceil((cy + MIDDLE * bh / 2) * sy))
    u0, v0, u1, v1 = max(u0, 0), max(v0, 0), min(max(u1, u0 + 1), w), min(max(v1, v0 + 1), h)
    if u0 >= w or v0 >= h:
        return None
    z = frame.depth[v0:v1, u0:u1]
    ok = np.isfinite(z) & (z > NEAR_M)
    if frame.good is not None:
        ok &= frame.good[v0:v1, u0:u1]
    kind = frame.depth_kind
    if kind == "lidar":
        near = ok & (z <= LIDAR_M)
        if near.sum() >= MIN_PIXELS:
            ok = near
        else:  # nothing in range: ARKit's estimate of what is farther
            kind = "estimate"
    if ok.sum() < min(MIN_PIXELS, z.size):
        return None
    vv, uu = np.nonzero(ok)
    zz = z[vv, uu].astype(float)
    # The nearer half of what is behind the middle: the thing, not what shows past it.
    keep = zz <= np.median(zz)
    uu, vv, zz = uu[keep] + u0, vv[keep] + v0, zz[keep]
    fx, fy, px, py = frame.K
    x = ((uu + 0.5) / sx - 0.5 - px) / fx * zz
    y = ((vv + 0.5) / sy - 0.5 - py) / fy * zz
    cam = np.stack([x, -y, -zz], 1)  # OpenCV (y down, z forward) to OpenGL (y up, z back)
    point = np.median(cam, 0)
    distance = float(np.median(zz))
    if frame.c2w is not None:
        point = frame.c2w[:3, :3] @ point + frame.c2w[:3, 3]
    return Place(point, distance, (float(bw / fx * distance), float(bh / fy * distance)), kind, int(keep.sum()))

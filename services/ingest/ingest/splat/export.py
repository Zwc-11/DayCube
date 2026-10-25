"""Splats for the viewer: a trained 3D Gaussian splat PLY (Brush's export, the standard layout) or a
LiDAR point cloud written as the 32-byte ``.splat`` records web splat renderers load.

A ``.splat`` record is position (3 floats), scale (3 floats, linear), colour and opacity (4 bytes)
and rotation (4 bytes, w x y z mapped from [-1, 1] to [0, 255]); records are ordered most visible
first (opacity times volume), so a renderer that loads progressively shows the scene's bulk at
once. View-dependent colour (the higher spherical harmonics) is dropped: the trained PLY keeps it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

SH_C0 = 0.28209479177387814
RECORD = np.dtype([("position", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rotation", "u1", 4)])
PLY_TYPES = {"float": "<f4", "float32": "<f4", "double": "<f8", "uchar": "u1", "uint8": "u1", "int": "<i4", "uint": "<u4", "short": "<i2", "ushort": "<u2"}


def read_ply(path: Path) -> np.ndarray:
    """The vertex element of a binary little-endian PLY as a structured array."""
    with path.open("rb") as stream:
        header = []
        while (line := stream.readline()) and line.strip() != b"end_header":
            header.append(line.decode("ascii").strip())
        if not header or header[0] != "ply" or "format binary_little_endian 1.0" not in header:
            raise ValueError(f"{path.name}: not a binary little-endian PLY")
        fields: list[tuple[str, str]] = []
        count = 0
        inside = False
        for line in header:
            parts = line.split()
            if parts[0] == "element":
                inside = parts[1] == "vertex"
                if inside:
                    count = int(parts[2])
            elif parts[0] == "property" and inside:
                if parts[1] == "list":
                    raise ValueError(f"{path.name}: list properties in the vertex element")
                fields.append((parts[2], PLY_TYPES[parts[1]]))
        return np.frombuffer(stream.read(count * np.dtype(fields).itemsize), dtype=fields, count=count)


def _records(position: np.ndarray, scale: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, quat_wxyz: np.ndarray, cap: int | None) -> np.ndarray:
    importance = alpha * np.prod(scale, axis=1)
    order = np.argsort(-importance, kind="stable")
    if cap is not None:
        order = order[:cap]
    out = np.zeros(len(order), RECORD)
    out["position"] = position[order]
    out["scale"] = scale[order]
    out["rgba"][:, :3] = np.clip(np.round(rgb[order] * 255), 0, 255)
    out["rgba"][:, 3] = np.clip(np.round(alpha[order] * 255), 0, 255)
    q = quat_wxyz[order]
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    out["rotation"] = np.clip(np.round(q * 128 + 128), 0, 255)
    return out


def splat_from_gaussians(ply: Path, target: Path, *, cap: int | None = 3_000_000, min_alpha: float = 1 / 255,
                         near: tuple[np.ndarray, float] | None = None) -> dict:  # fmt: skip
    """A trained splat PLY as ``.splat``; returns counts and the bounds of the bulk, of the splats
    within ``near`` (centre, radius) when given: the sky shell is not the scene."""
    v = read_ply(ply)
    position = np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float32)
    scale = np.exp(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], 1)).astype(np.float32)
    rgb = 0.5 + SH_C0 * np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], 1)
    alpha = 1 / (1 + np.exp(-v["opacity"].astype(np.float64)))
    quat = np.stack([v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], 1).astype(np.float64)
    keep = (alpha >= min_alpha) & np.isfinite(position).all(1) & np.isfinite(scale).all(1)
    records = _records(position[keep], scale[keep], rgb[keep], alpha[keep], quat[keep], cap)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(records.tobytes())
    inside = records["position"]
    if near is not None:
        inside = inside[np.linalg.norm(inside - near[0], axis=1) < near[1]]
    return {"splats": int(len(records)), "trained": int(len(v)), "bytes": target.stat().st_size, **bounds(inside)}


def splat_from_points(clouds: list[tuple[Path, float]], target: Path, *, alpha: float = 0.9) -> dict:
    """Coloured point clouds, each as round ``.splat`` dots of its size across (metres): what the
    scan looks like in 3D before any training (the LiDAR's returns, and the larger estimates past
    its range)."""
    parts = [(read_ply(ply), size) for ply, size in clouds if ply.is_file()]
    position = np.concatenate([np.stack([v["x"], v["y"], v["z"]], 1) for v, _ in parts]).astype(np.float32)
    rgb = np.concatenate([np.stack([v["red"], v["green"], v["blue"]], 1) for v, _ in parts]).astype(np.float64) / 255
    scale = np.concatenate([np.full((len(v), 3), size / 2, np.float32) for v, size in parts])
    n = len(position)
    quat = np.tile([1.0, 0.0, 0.0, 0.0], (n, 1))
    records = _records(position, scale, rgb, np.full(n, alpha), quat, None)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(records.tobytes())
    return {"splats": int(n), "bytes": target.stat().st_size, **bounds(position)}


def bounds(position: np.ndarray) -> dict:
    """Where the bulk of the scene is (2nd to 98th percentile), for framing the first view."""
    if not len(position):
        return {"bounds": None}
    lo, hi = np.percentile(position, 2, axis=0), np.percentile(position, 98, axis=0)
    return {"bounds": {"min": [round(float(x), 3) for x in lo], "max": [round(float(x), 3) for x in hi]}}

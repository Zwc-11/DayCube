"""A scan's surfaces as a coloured mesh: the LiDAR's depth maps fused into a truncated signed
distance field at ARKit's poses (Open3D ``ScalableTSDFVolume``, the ``mesh`` extra), coloured from
the video at the depth maps' resolution, then cleaned and simplified for the viewer.

The splat shows how the walk looked; the mesh is where its surfaces are, metric and with no
training: the ground under the follow camera, and geometry for the world-model data.

Every ``every``-th depth map is fused (medium or high confidence, within ``max_depth_m``); the
others are kept back to check the result: the median distance from their LiDAR points to the mesh.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import dataset

GL_TO_CV = np.diag([1.0, -1.0, -1.0, 1.0])


def available() -> bool:
    try:
        import open3d  # noqa: F401
    except ImportError:
        return False
    return True


def build(
    capture: Path,
    target: Path,
    *,
    video: Path | None = None,
    voxel_m: float = 0.04,
    max_depth_m: float = 5.0,
    min_confidence: int = 1,
    every: int = 3,
    max_triangles: int = 600_000,
    min_component: int = 400,
    say: Callable[[str], None] = print,
) -> dict:
    """Write the mesh to ``target`` (``.glb``); returns its counts and the check."""
    import open3d as o3d

    started = time.monotonic()
    video = video or capture / "rgb.mp4"
    width, height, count = dataset._video_size(video)
    odo = dataset.read_odometry(capture, width, height)
    n = min(count, len(odo.frame))
    depth_of = {int(p.stem): p for p in (capture / "depth").glob("*.png")}
    usable = [
        i for i in range(n) if int(odo.frame[i]) in depth_of and np.isfinite(odo.c2w[i, 0, 0]) and odo.t[i] >= 0.5
    ]
    fused = np.array(usable[::every])
    held = np.array([i for k, i in enumerate(usable) if k % every == every // 2][::4])
    say(f"Fusing {len(fused)} depth maps into a mesh ({voxel_m * 100:.0f} cm voxels)")
    colours, decoded = dataset.frames_at(video, fused)
    fused, colours = fused[decoded], colours[decoded]
    depths = dataset.read_maps([depth_of[int(odo.frame[i])] for i in fused], "gray16le", "<u2")
    conf_dir = capture / "confidence"
    conf_paths = [conf_dir / f"{int(odo.frame[i]):06d}.png" for i in fused]
    confs = dataset.read_maps(conf_paths, "gray", "u1") if all(p.is_file() for p in conf_paths) else None
    s = dataset.DEPTH_W / width

    # Open3D's tensor TSDF (VoxelBlockGrid): its legacy ScalableTSDFVolume fused nothing at all
    # in the 0.20 Linux wheel, even from one frame with an identity pose (2026-10-25).
    cpu = o3d.core.Device("CPU:0")
    grid = o3d.t.geometry.VoxelBlockGrid(
        attr_names=("tsdf", "weight", "color"),
        attr_dtypes=(o3d.core.float32, o3d.core.float32, o3d.core.float32),
        attr_channels=((1), (1), (3)),
        voxel_size=voxel_m,
        block_resolution=8,
        block_count=60_000,
        device=cpu,
    )
    for k, i in enumerate(fused):
        fx, fy, cx, cy = odo.K[i]
        K = o3d.core.Tensor(
            [[fx * s, 0, (cx + 0.5) * s - 0.5], [0, fy * s, (cy + 0.5) * s - 0.5], [0, 0, 1]], o3d.core.float64
        )
        extrinsic = o3d.core.Tensor(np.linalg.inv(odo.c2w[i] @ GL_TO_CV), o3d.core.float64)
        depth_np = depths[k].copy()
        if confs is not None:
            depth_np[confs[k] < min_confidence] = 0
        if np.count_nonzero((depth_np > 200) & (depth_np < max_depth_m * 1000)) < 200:
            continue  # nothing near enough to fuse (the sky, the LiDAR's range passed)
        depth = o3d.t.geometry.Image(np.ascontiguousarray(depth_np)).to(cpu)
        colour = o3d.t.geometry.Image(np.ascontiguousarray(colours[k])).to(cpu)
        blocks = grid.compute_unique_block_coordinates(
            depth, K, extrinsic, 1000.0, max_depth_m, trunc_voxel_multiplier=4.0
        )
        grid.integrate(blocks, depth, colour, K, extrinsic, 1000.0, max_depth_m, trunc_voxel_multiplier=4.0)
    mesh = grid.extract_triangle_mesh().to_legacy()
    raw = len(mesh.triangles)
    # Small islands are noise (a leaf, a hand, a passer-by fused once).
    clusters, sizes, _ = mesh.cluster_connected_triangles()
    clusters, sizes = np.asarray(clusters), np.asarray(sizes)
    mesh.remove_triangles_by_mask(sizes[clusters] < min_component)
    mesh.remove_unreferenced_vertices()
    if len(mesh.triangles) > max_triangles:
        mesh = mesh.simplify_quadric_decimation(max_triangles)
    mesh.compute_vertex_normals()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_triangle_mesh(str(target), mesh, write_ascii=False, compressed=False):
        raise RuntimeError(f"could not write {target}")
    check = _check(mesh, capture, odo, held, depth_of, max_depth_m, min_confidence, s)
    vertices = np.asarray(mesh.vertices)
    lo, hi = (
        (np.percentile(vertices, 2, axis=0), np.percentile(vertices, 98, axis=0)) if len(vertices) else (None, None)
    )
    say(f"Mesh: {len(mesh.triangles):,} triangles (from {raw:,}); held-out LiDAR points lie a median "
        f"{check['median_m'] * 100:.1f} cm from it" if check.get("median_m") is not None else "Mesh written")  # fmt: skip
    return {
        "triangles": len(mesh.triangles),
        "vertices": len(vertices),
        "raw_triangles": raw,
        "fused_frames": len(fused),
        "voxel_m": voxel_m,
        "bytes": target.stat().st_size,
        "bounds": None
        if lo is None
        else {"min": [round(float(v), 3) for v in lo], "max": [round(float(v), 3) for v in hi]},
        "check": check,
        "seconds": round(time.monotonic() - started, 1),
    }


def _check(
    mesh, capture: Path, odo, held: np.ndarray, depth_of: dict, max_depth_m: float, min_confidence: int, s: float
) -> dict:  # noqa: ANN001
    """Distance from LiDAR points of depth maps that were not fused to the mesh."""
    import open3d as o3d

    if not len(held) or not len(mesh.triangles):
        return {"points": 0, "median_m": None, "p90_m": None}
    depths = dataset.read_maps([depth_of[int(odo.frame[i])] for i in held], "gray16le", "<u2")
    conf_paths = [capture / "confidence" / f"{int(odo.frame[i]):06d}.png" for i in held]
    confs = dataset.read_maps(conf_paths, "gray", "u1") if all(p.is_file() for p in conf_paths) else None
    rng = np.random.default_rng(0)
    points = []
    for k, i in enumerate(held):
        d = depths[k].astype(np.float32) / 1000.0
        mask = (d > 0.2) & (d < max_depth_m)
        if confs is not None:
            mask &= confs[k] >= max(min_confidence, 2)
        pts = dataset.back_project(d, odo.K[i], odo.c2w[i], mask, s)
        if len(pts):
            points.append(pts[rng.choice(len(pts), min(400, len(pts)), replace=False)])
    if not points:
        return {"points": 0, "median_m": None, "p90_m": None}
    xyz = np.concatenate(points).astype(np.float32)
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
    distance = scene.compute_distance(o3d.core.Tensor(xyz)).numpy()
    return {"points": int(len(xyz)), "frames": int(len(held)), "median_m": round(float(np.median(distance)), 4),
            "p90_m": round(float(np.quantile(distance, 0.9)), 4)}  # fmt: skip

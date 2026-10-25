"""A scan moment's splats: the LiDAR preview at once, then the trained splat.

``splat_moment`` builds the training set in ``vault/derived/splat/<date>/<moment>/`` (frames,
poses, starting points), refuses to go on if the poses disagree with the LiDAR, writes
``moments/<id>/splat/lidar.splat`` (the LiDAR's returns as dots, viewable before any training) and
``cameras.json`` (where the camera was, for the viewer to follow the walk), then trains with Brush
and writes ``splat/scene.splat``. ``scene.json`` says what there is (``splat``) at every stage, so
the viewer shows the best available and a failed training leaves the preview in place.

The capture's depth comes from the moment's ``capture/`` folder (kept by the importer since
2026-10-25), else from the phone inbox's ``imported/`` copy of the same capture (same video name and
size), else from ``--capture``.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..vault import Vault, read_json
from . import brush, dataset, export

CAMERA_EVERY = 3  # frames between camera poses kept for the viewer


class SplatError(RuntimeError):
    pass


def find_capture(vault: Vault, moment: Path) -> Path | None:
    """Where the moment's capture, with its depth, still is."""
    own = moment / "capture"
    if (own / "odometry.csv").is_file() and (own / "depth").is_dir():
        return own
    source = (read_json(moment / "scene.json") or {}).get("source") or {}
    name, size = source.get("name"), source.get("bytes")
    imported = vault.root / "inbox" / "phone" / "imported"
    if not name or not imported.is_dir():
        return None
    for video in sorted(imported.glob(f"*/*/{name}"), reverse=True):
        folder = video.parent
        if video.stat().st_size == size and (folder / "odometry.csv").is_file() and (folder / "depth").is_dir():
            return folder
    return None


def cameras(capture: Path, width: int, height: int) -> dict:
    """The camera's position and the way it looked (world metres, y up), every few frames."""
    odo = dataset.read_odometry(capture, width, height)
    keep = np.arange(0, len(odo.t), CAMERA_EVERY)
    forward = -odo.c2w[keep, :3, 2]  # OpenGL cameras look along -z
    up = odo.c2w[keep, :3, 1]
    fy = float(np.median(odo.K[:, 1]))
    return {
        "t": [round(float(v), 3) for v in odo.t[keep]],
        "position": [[round(float(v), 3) for v in p] for p in odo.c2w[keep, :3, 3]],
        "forward": [[round(float(v), 4) for v in f] for f in forward],
        "up": [[round(float(v), 4) for v in u] for u in up],
        # The video is landscape; held upright the phone saw the long side vertically.
        "fov_long_deg": round(float(np.degrees(2 * np.arctan(max(width, height) / 2 / fy))), 2),
        "fov_short_deg": round(float(np.degrees(2 * np.arctan(min(width, height) / 2 / fy))), 2),
    }


def _write_scene(vault: Vault, moment: Path, splat: dict) -> None:
    scene = read_json(moment / "scene.json")
    scene["splat"] = splat
    with vault.lock:
        vault.write_json(moment / "scene.json", scene)


def splat_moment(
    vault: Vault,
    date: str,
    moment_id: str,
    *,
    capture: Path | None = None,
    steps: int = 30_000,
    frames: int = 420,
    train: bool = True,
    say: Callable[[str], None] = print,
) -> dict:
    moment = vault.day_path(date) / "moments" / moment_id
    if not (moment / "scene.json").is_file():
        raise SplatError(f"No moment {moment_id} on {date}")
    scene = read_json(moment / "scene.json")
    if scene.get("kind") != "camera-path":
        raise SplatError(f"{moment_id} is not a scan")
    capture = capture or find_capture(vault, moment)
    if capture is None:
        raise SplatError(f"{moment_id}: the capture's depth folder is not in the vault; give it with --capture")
    video = moment / scene["video"] if (moment / str(scene.get("video"))).is_file() else capture / "rgb.mp4"
    work = vault.root / "derived" / "splat" / date / moment_id
    train_dir = work / "train"
    if train_dir.exists():
        shutil.rmtree(train_dir)
    train_dir.mkdir(parents=True)
    started = time.monotonic()
    report = dataset.build(capture, train_dir, target=frames, video=video, say=say)
    (work / "dataset.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    if not report["check"].get("ok"):
        raise SplatError(f"The scan's poses disagree with its depth ({report['check']}); not training")
    say(f"Poses agree with the LiDAR: depth off by {report['check']['depth_error'] * 100:.1f}% "
        f"(at a wrong pose: {report['check']['wrong_pose_error'] * 100:.1f}%)")  # fmt: skip
    out = moment / "splat"
    out.mkdir(exist_ok=True)
    width, height = scene.get("size") or [1920, 1440]
    (out / "cameras.json").write_text(json.dumps(cameras(capture, width, height)), encoding="utf-8")
    init = report["init"]
    lidar = export.splat_from_points(
        [(train_dir / "lidar.ply", init["voxel_m"] * 1.2), (train_dir / "far.ply", init.get("far_voxel_m", 0.1) * 1.2)],
        out / "lidar.splat",
    )
    splat = {
        "status": "lidar",
        "lidar": "splat/lidar.splat",
        "cameras": "splat/cameras.json",
        "points": lidar["splats"],
        "bounds": lidar["bounds"],
        "dataset": {k: report[k] for k in ("frames", "size", "path_m", "extent_m", "init", "check")},
        "prepared_s": round(time.monotonic() - started, 1),
    }
    previous = scene.get("splat") or {}
    if previous.get("trained") and (moment / previous["trained"]).is_file():
        splat |= {k: previous[k] for k in ("trained", "training", "splats") if k in previous} | {"status": "trained"}
    _write_scene(vault, moment, splat)
    say(f"LiDAR preview: {lidar['splats']:,} points in {out / 'lidar.splat'}")
    if not train:
        return splat

    splat["status"] = "training"
    _write_scene(vault, moment, splat)

    def progress(p: brush.Progress) -> None:
        psnr = f", PSNR {p.psnr[-1][1]:.1f} dB" if p.psnr else ""
        say(f"Training {p.step:,}/{p.total:,} steps, {p.splats:,} splats, {p.seconds / 60:.0f} min{psnr}")

    try:
        result = brush.train(train_dir, work / "export", steps=steps, max_resolution=max(report["size"]),
                             log=work / "brush.log", on_progress=progress)  # fmt: skip
    except Exception as error:
        splat["status"] = "failed"
        splat["error"] = str(error)[:2000]
        _write_scene(vault, moment, splat)
        raise
    trained = export.splat_from_gaussians(work / "export" / "splat.ply", out / "scene.splat")
    splat |= {
        "status": "trained",
        "trained": "splat/scene.splat",
        "splats": trained["splats"],
        "bounds": trained["bounds"] or splat["bounds"],
        "training": {
            "trainer": f"Brush {brush.VERSION}",
            "steps": steps,
            "minutes": round(result.seconds / 60, 1),
            "psnr_db": result.psnr[-1][1] if result.psnr else None,
            "ssim": result.ssim,
            "held_out_frames": report["frames"] // 25,
        },
    }
    splat.pop("error", None)
    _write_scene(vault, moment, splat)
    # The frames can be made again from the video; the trained PLY (with view-dependent colour) stays.
    shutil.rmtree(train_dir / "images", ignore_errors=True)
    say(f"Trained: {trained['splats']:,} splats in {result.seconds / 60:.0f} min"
        + (f", PSNR {result.psnr[-1][1]:.1f} dB on held-out frames" if result.psnr else ""))  # fmt: skip
    return splat


def scans(vault: Vault, date: str) -> list[str]:
    """The day's scan moments."""
    folder = vault.day_path(date) / "moments"
    return sorted(p.parent.name for p in folder.glob("mo_scan_*/scene.json")) if folder.is_dir() else []

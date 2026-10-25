"""Scans into 3D: the training set from a capture (frames, OpenGL poses from Stray's OpenCV ones,
the LiDAR's points, the pose check), the splats the viewer loads, and the whole run on a moment
with a stand-in for Brush (no GPU in tests)."""

from __future__ import annotations

import json
import math
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from ingest.phone import stray
from ingest.splat import brush, dataset, export
from ingest.splat.run import SplatError, find_capture, splat_moment
from ingest.vault import Vault, read_json

pytestmark = pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs FFmpeg")

W, H, F = 640, 480, 500.0  # video size and focal length
GROUND_Y, WALL_X = -1.4, 2.0
FRAMES = 90
SPEED = 1.2  # m/s along -z (north), looking ahead


def _pose(k: int, yaw_deg: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Position and OpenCV camera-to-world rotation (x right, y down, z forward) at frame k: walking
    along world -z with the camera looking the way it walks, turned ``yaw_deg`` to the left."""
    t = k / 30
    position = np.array([0.0, 0.0, -SPEED * t])
    a = math.radians(yaw_deg)
    forward = np.array([-math.sin(a), 0.0, -math.cos(a)])
    down = np.array([0.0, -1.0, 0.0])
    right = np.cross(down, forward)
    return position, np.stack([right, down, forward], 1)


def _quat(R: np.ndarray) -> tuple[float, float, float, float]:
    """x, y, z, w of a rotation matrix."""
    w = math.sqrt(max(0.0, 1 + R[0, 0] + R[1, 1] + R[2, 2])) / 2
    if w > 1e-6:
        return ((R[2, 1] - R[1, 2]) / (4 * w), (R[0, 2] - R[2, 0]) / (4 * w), (R[1, 0] - R[0, 1]) / (4 * w), w)
    x = math.sqrt(max(0.0, 1 + R[0, 0] - R[1, 1] - R[2, 2])) / 2
    return (x, (R[0, 1] + R[1, 0]) / (4 * x), (R[0, 2] + R[2, 0]) / (4 * x), (R[2, 1] - R[1, 2]) / (4 * x))


def _cast(position: np.ndarray, R: np.ndarray, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Ray-cast the scene from a camera (OpenCV rotation R) at w x h: z-depth in metres (0: nothing
    within 30 m) and the world point each pixel sees. The scene: the ground, and a wall to the
    right with a recess every other 1.5 m (it changes along the walk, as real places do)."""
    s = w / W
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    x = ((u + 0.5) / s - 0.5 - W / 2) / F
    y = ((v + 0.5) / s - 0.5 - H / 2) / F
    rays = np.stack([x, y, np.ones_like(x)], -1) @ R.T  # world direction per unit of z-depth
    with np.errstate(divide="ignore", invalid="ignore"):
        t_ground = np.where(rays[..., 1] < -1e-6, (GROUND_Y - position[1]) / rays[..., 1], np.inf)
        t_wall = np.where(rays[..., 0] > 1e-6, (WALL_X - position[0]) / rays[..., 0], np.inf)
        along = np.nan_to_num(-(position[2] + t_wall * rays[..., 2]) / 1.5, posinf=0, neginf=0)
        t_back = np.where(rays[..., 0] > 1e-6, (WALL_X + 1.5 - position[0]) / rays[..., 0], np.inf)
    t_wall = np.where(np.floor(along) % 2 == 1, t_back, t_wall)
    z = np.minimum(t_ground, t_wall)
    z = np.where(np.isfinite(z) & (z < 30), z, 0.0)
    return z, position + rays * z[..., None]


def _texture(z: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Colours of world points: a 25 cm checker with a finer pattern on it (textured enough for
    the colour check), sky where nothing was hit."""
    p = points * 4
    checker = (np.floor(p[..., 0]) + np.floor(p[..., 1]) + np.floor(p[..., 2])) % 2
    fine = 0.5 + 0.5 * np.sin(p[..., 0] * 9.0) * np.sin(p[..., 2] * 7.0 + p[..., 1] * 5.0)
    rgb = np.stack([60 + 150 * checker, 50 + 160 * fine, 200 - 120 * checker * fine], -1)
    rgb[z == 0] = [140, 180, 230]
    return np.clip(rgb, 0, 255).astype(np.uint8)


def _depth(position: np.ndarray, R: np.ndarray) -> np.ndarray:
    return _cast(position, R, dataset.DEPTH_W, dataset.DEPTH_H)[0]


def make_capture(folder: Path, *, frames: int = FRAMES, start: datetime | None = None) -> Path:
    """A Stray Scanner folder: the video, depth and confidence PNGs ray-cast from a textured ground
    and wall, and the poses in Stray's OpenCV convention."""
    folder.mkdir(parents=True)
    began = start or datetime(2026, 10, 21, 18, 20, tzinfo=UTC)
    video = np.stack([_texture(*_cast(*_pose(k), W, H)) for k in range(frames)])
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "30",
         "-i", "pipe:0", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "16",
         "-metadata", f"creation_time={began.strftime('%Y-%m-%dT%H:%M:%S.000000Z')}", str(folder / "rgb.mp4")],
        input=video.tobytes(), check=True,
    )  # fmt: skip
    rows = ["timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy"]
    depths = []
    for k in range(frames):
        p, R = _pose(k)
        q = _quat(R)
        rows.append(f"{1000 + k / 30:.4f}, {k:06d}, {p[0]:.4f}, {p[1]:.4f}, {p[2]:.4f}, "
                    f"{q[0]:.6f}, {q[1]:.6f}, {q[2]:.6f}, {q[3]:.6f}, {F}, {F}, {W / 2}, {H / 2}")  # fmt: skip
        depths.append(np.round(_depth(p, R) * 1000).astype("<u2"))
    (folder / "odometry.csv").write_text("\n".join(rows) + "\n")
    (folder / "camera_matrix.csv").write_text(f"{F}, 0.0, {W / 2}\n0.0, {F}, {H / 2}\n0.0, 0.0, 1.0\n")
    for name, data, fmt in (("depth", np.stack(depths), "gray16le"),
                            ("confidence", np.where(np.stack(depths) > 0, 2, 0).astype("u1"), "gray")):  # fmt: skip
        (folder / name).mkdir()
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", fmt, "-s", f"{dataset.DEPTH_W}x{dataset.DEPTH_H}",
             "-i", "pipe:0", "-start_number", "0", str(folder / name / "%06d.png")],
            input=data.tobytes(), check=True,
        )  # fmt: skip
    return folder


def test_a_capture_becomes_a_training_set_with_opengl_poses_and_lidar_points(tmp_path: Path) -> None:
    capture = make_capture(tmp_path / "scan")
    report = dataset.build(capture, tmp_path / "train", target=30, long_side=320, say=lambda _: None)
    assert report["frames"] == 30 and report["size"] == [320, 240]
    assert report["check"]["ok"] and report["check"]["depth_error"] < 0.01
    assert report["check"]["colour_error"] < 0.5 * report["check"]["mirrored_colour_error"]
    transforms = json.loads((tmp_path / "train" / "transforms.json").read_text())
    assert transforms["ply_file_path"] == "init.ply"
    frame = transforms["frames"][0]
    assert (tmp_path / "train" / frame["file_path"]).is_file()
    assert frame["fl_x"] == pytest.approx(F / 2) and frame["w"] == 320
    # OpenGL: the camera looks along its -z axis, which points the way it walks (world -z).
    c2w = np.array(frame["transform_matrix"])
    assert (-c2w[:3, 2]) @ np.array([0, 0, -1.0]) == pytest.approx(1.0, abs=1e-4)
    assert c2w[:3, 1] @ np.array([0, 1.0, 0]) == pytest.approx(1.0, abs=1e-4)  # y up
    # The LiDAR's points are on the ground and the wall they were rendered from.
    points = export.read_ply(tmp_path / "train" / "lidar.ply")
    xyz = np.stack([points["x"], points["y"], points["z"]], 1)
    on = np.minimum(np.abs(xyz[:, 1] - GROUND_Y), np.minimum(np.abs(xyz[:, 0] - WALL_X), np.abs(xyz[:, 0] - WALL_X - 1.5)))
    assert len(xyz) > 1000 and np.quantile(on, 0.95) < 0.05
    assert report["init"]["far_points"] > 0  # the wall and ground past 5.5 m
    # The sky shell: far out, above -10 degrees, coloured from frames that see it, part of init.ply.
    sky = export.read_ply(tmp_path / "train" / "sky.ply")
    sxyz = np.stack([sky["x"], sky["y"], sky["z"]], 1).astype(float)
    path = np.array([np.array(f["transform_matrix"])[:3, 3] for f in transforms["frames"]])
    reach = np.linalg.norm(sxyz - path.mean(0), axis=1)
    assert len(sxyz) == report["init"]["sky_points"] > 0
    assert reach == pytest.approx(np.full(len(reach), report["init"]["sky_radius_m"]), rel=0.01)
    assert report["init"]["sky_radius_m"] >= 100
    assert (sxyz[:, 1] - path.mean(0)[1] >= np.sin(np.radians(-10)) * reach - 0.5).all()
    assert (np.stack([sky["red"], sky["green"], sky["blue"]], 1).sum(1) > 0).mean() > 0.9
    init = export.read_ply(tmp_path / "train" / "init.ply")
    assert len(init) == report["init"]["points"] + report["init"]["far_points"] + report["init"]["sky_points"]


def test_the_check_refuses_poses_in_the_wrong_convention(tmp_path: Path) -> None:
    capture = make_capture(tmp_path / "scan")
    # The same walk with the camera looking backwards: what reading Stray's poses as OpenGL did.
    lines = (capture / "odometry.csv").read_text().splitlines()
    turned = [lines[0]]
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(",")]
        _, R = _pose(int(cells[1]))
        q = _quat(R @ np.diag([1.0, -1.0, -1.0]))
        cells[5:9] = [f"{v:.6f}" for v in q]
        turned.append(", ".join(cells))
    (capture / "odometry.csv").write_text("\n".join(turned) + "\n")
    report = dataset.build(capture, tmp_path / "train", target=30, long_side=320, say=lambda _: None)
    assert report["check"]["ok"] is False


def test_the_scan_heading_is_the_way_the_camera_looks(tmp_path: Path) -> None:
    capture = make_capture(tmp_path / "scan", frames=30)
    scan = stray.read({"video": capture / "rgb.mp4", "odometry": capture / "odometry.csv"})
    scan.fit = {"rotation_deg": 0.0, "offset_s": 0, "rmse_m": 0.1}  # ARKit -z is north
    assert np.allclose(stray.heading(scan), 0.0, atol=0.5) or np.allclose(stray.heading(scan), 360.0, atol=0.5)
    scan.rotation = np.array([_quat(_pose(0, yaw_deg=90)[1])] * len(scan.t))  # turned to face west
    assert np.allclose(stray.heading(scan), 270.0, atol=0.5)


def test_splats_for_the_viewer(tmp_path: Path) -> None:
    n = 5
    fields = ["x", "y", "z", "scale_0", "scale_1", "scale_2", "opacity", "rot_0", "rot_1", "rot_2", "rot_3",
              "f_dc_0", "f_dc_1", "f_dc_2"] + [f"f_rest_{k}" for k in range(9)]  # fmt: skip
    data = np.zeros(n, dtype=[(f, "<f4") for f in fields])
    data["x"] = np.arange(n)
    data["scale_0"] = data["scale_1"] = data["scale_2"] = np.log([0.01, 0.02, 0.03, 0.04, 0.05])
    data["opacity"] = [5.0, 5.0, 5.0, 5.0, -10.0]  # the last is all but invisible
    data["rot_0"] = 2.0  # w, unnormalized
    data["f_dc_0"] = (1.0 - 0.5) / export.SH_C0  # red 1.0
    ply = tmp_path / "splat.ply"
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 5\n" + "".join(f"property float {f}\n" for f in fields) + "end_header\n"
    ply.write_bytes(header.encode() + data.tobytes())
    info = export.splat_from_gaussians(ply, tmp_path / "scene.splat")
    records = np.frombuffer((tmp_path / "scene.splat").read_bytes(), export.RECORD)
    assert info["splats"] == 4 and len(records) == 4 and info["bytes"] == 4 * 32
    assert list(records["position"][:, 0]) == [3, 2, 1, 0]  # biggest first
    assert records["scale"][0] == pytest.approx([0.04] * 3)
    assert list(records["rgba"][0]) == [255, 128, 128, 253]
    assert list(records["rotation"][0]) == [255, 128, 128, 128]  # w=1 normalized


def test_splats_covering_the_walked_path_are_left_out(tmp_path: Path) -> None:
    path = np.array([[0, 0, 0], [0, 0, -10.0]])
    position = np.array([[0, 0, -5.0], [2, 0, -5.0], [1.5, 0, -5.0], [0.1, 0.1, -20.0]], np.float32)
    scale = np.array([[0.02] * 3, [0.05] * 3, [1.0] * 3, [0.02] * 3], np.float32)
    # on the path; 2 m beside it and small; 1.5 m beside it but 1 m across (2 sigma reaches); past its end
    assert export.covering(position, scale, path).tolist() == [True, False, True, False]


def fake_brush(folder: Path) -> Path:
    """A stand-in for Brush that logs like it and exports a two-splat PLY."""
    script = folder / "fake_brush.py"
    script.write_text(
        "import sys, pathlib, numpy as np\n"
        "args = sys.argv[1:]\n"
        "out = pathlib.Path(args[args.index('--export-path') + 1]) / args[args.index('--export-name') + 1]\n"
        "steps = int(args[args.index('--total-steps') + 1])\n"
        "print('Loaded dataset with 29 training, 1 eval views', flush=True)\n"
        "for s in range(0, steps + 1, steps // 4): print(f'{s}/{steps}', end='\\r', flush=True)\n"
        "print('Refine iter 200, 12345 splats', flush=True)\n"
        "print(f'Eval iter {steps}: PSNR 27.5, ssim 0.91', flush=True)\n"
        "f = ['x','y','z','scale_0','scale_1','scale_2','opacity','rot_0','rot_1','rot_2','rot_3','f_dc_0','f_dc_1','f_dc_2']\n"
        "d = np.zeros(2, dtype=[(k, '<f4') for k in f]); d['rot_0'] = 1; d['opacity'] = 3; d['x'] = 3\n"
        "for k in ('scale_0', 'scale_1', 'scale_2'): d[k] = -4  # 2 cm, beside the walk\n"
        "h = 'ply\\nformat binary_little_endian 1.0\\nelement vertex 2\\n' + ''.join(f'property float {k}\\n' for k in f) + 'end_header\\n'\n"
        "out.write_bytes(h.encode() + d.tobytes())\n"
    )
    launcher = folder / ("fake_brush.cmd" if os.name == "nt" else "fake_brush")
    if os.name == "nt":
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n')
    else:
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    return launcher


def test_a_scan_moment_gets_its_lidar_preview_then_its_trained_splat(vault: Vault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ingest.phone.importer import import_phone

    capture = make_capture(tmp_path / "drop" / "scan")
    import_phone(vault, [capture], transcribe=False, say=lambda _: None)
    moment = next(vault.root.glob("*/moments/mo_scan_*"))
    day = moment.parent.parent
    scene = read_json(moment / "scene.json")
    assert scene["capture"]["depth_frames"] == FRAMES  # the importer keeps depth and poses
    assert find_capture(vault, moment) == moment / "capture"

    monkeypatch.setenv("DAYCUBE_BRUSH", str(fake_brush(tmp_path)))
    lines: list[str] = []
    splat = splat_moment(vault, day.name, moment.name, steps=400, frames=30, say=lines.append)
    assert splat["status"] == "trained" and splat["trained"] == "splat/scene.splat"
    assert splat["training"]["psnr_db"] == 27.5 and splat["training"]["trainer"] == f"Brush {brush.VERSION}"
    assert (moment / "splat" / "lidar.splat").stat().st_size == 32 * splat["points"]
    assert splat["points"] == splat["dataset"]["init"]["points"] + splat["dataset"]["init"]["far_points"]
    assert (moment / "splat" / "scene.splat").stat().st_size == 32 * 2
    cams = json.loads((moment / "splat" / "cameras.json").read_text())
    assert len(cams["t"]) == math.ceil(FRAMES / 3) and cams["forward"][0] == pytest.approx([0, 0, -1], abs=1e-3)
    assert read_json(moment / "scene.json")["splat"]["status"] == "trained"
    assert any("Poses agree with the LiDAR" in line for line in lines)

    # Importing the same capture again keeps what the GPU made.
    import_phone(vault, [capture], transcribe=False, say=lambda _: None)
    assert (moment / "splat" / "scene.splat").is_file()
    assert read_json(moment / "scene.json")["splat"]["status"] == "trained"


def test_a_failed_training_keeps_the_preview_and_says_why(vault: Vault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ingest.phone.importer import import_phone

    capture = make_capture(tmp_path / "drop" / "scan")
    import_phone(vault, [capture], transcribe=False, say=lambda _: None)
    moment = next(vault.root.glob("*/moments/mo_scan_*"))
    day = moment.parent.parent
    broken = tmp_path / ("broken.cmd" if os.name == "nt" else "broken")
    broken.write_text("@echo out of memory\n@exit /b 3\n" if os.name == "nt" else "#!/bin/sh\necho out of memory\nexit 3\n")
    broken.chmod(broken.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("DAYCUBE_BRUSH", str(broken))
    with pytest.raises(RuntimeError, match="out of memory"):
        splat_moment(vault, day.name, moment.name, steps=100, frames=30, say=lambda _: None)
    splat = read_json(moment / "scene.json")["splat"]
    assert splat["status"] == "failed" and "out of memory" in splat["error"]
    assert (moment / "splat" / "lidar.splat").is_file()
    with pytest.raises(SplatError, match="not a scan"):
        audio = moment.parent / "mo_voice_x"
        audio.mkdir()
        (audio / "scene.json").write_text('{"kind": "audio"}')
        splat_moment(vault, day.name, "mo_voice_x", say=lambda _: None)


def test_brush_progress_is_read_from_its_log() -> None:
    p = brush.Progress(total=30000)
    for line in ["Loaded dataset with 400 training, 16 eval views", "  1200/30000 [00:30]", "Refine iter 1200, 815433 splats",
                 "Eval iter 3000: PSNR 23.41, ssim 0.712", "15000/30000"]:  # fmt: skip
        p.read(line)
    assert (p.step, p.splats, p.psnr, p.ssim) == (15000, 815433, [(3000, 23.41)], 0.712)

    # Brush 0.3.0 headless (the RTX 5070, 2026-10-25): no progress bar, only its log
    p = brush.Progress(total=30000)
    for line in ["[2026-10-25T15:37:31Z INFO  brush_cli] Loaded dataset with 403 training, 17 eval views",
                 "[2026-10-25T15:38:59Z INFO  brush_cli] Refine iter 1801, 2154157 splats."]:  # fmt: skip
        p.read(line)
    assert (p.step, p.splats) == (1800, 2154157)


def test_new_scans_get_their_preview_then_train_when_the_pc_is_free(vault: Vault, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ingest.jobs.stages import discover
    from ingest.jobs.store import Jobs
    from ingest.jobs.worker import run_next
    from ingest.phone.importer import import_phone

    capture = make_capture(tmp_path / "drop" / "scan")
    import_phone(vault, [capture], transcribe=False, say=lambda _: None)
    moment = next(vault.root.glob("*/moments/mo_scan_*"))
    monkeypatch.delenv("DAYCUBE_BRUSH", raising=False)
    monkeypatch.delenv("DAYCUBE_SPLAT_TRAIN", raising=False)
    monkeypatch.setenv("DAYCUBE_TOOLS", str(tmp_path / "no-tools"))  # no Brush installed
    jobs = Jobs.of(vault.root)
    day, night = datetime(2026, 10, 25, 14, 0), datetime(2026, 10, 25, 3, 0)
    quiet = {"say": lambda _: None}

    assert len(discover(vault, jobs)) == 2 and discover(vault, jobs) == []  # mesh and preview, queued once
    ran = {run_next(vault, jobs, "cpu", "cpu", **quiet)["stage"] for _ in range(2)}
    assert ran == {"scan_mesh", "splat_preview"}
    assert read_json(moment / "scene.json")["splat"]["status"] == "lidar"
    assert read_json(moment / "scene.json")["mesh"]["status"] == "made" and (moment / "mesh" / "scene.glb").is_file()
    assert jobs.status()["stages"]["splat_train"]["queued"] == 1  # the preview leads to training
    # Without Brush it never trains, idle or not: installing it is your call (daycube splat).
    assert run_next(vault, jobs, "gpu", "gpu", idle_s=3600, now=day, **quiet) is None
    monkeypatch.setenv("DAYCUBE_BRUSH", str(fake_brush(tmp_path)))
    assert run_next(vault, jobs, "gpu", "gpu", idle_s=60, now=day, **quiet) is None  # you are at the PC
    result = run_next(vault, jobs, "gpu", "gpu", idle_s=60, now=night, **quiet)
    assert result["did"] == "done" and result["outputs"]["psnr_db"] == 27.5
    assert read_json(moment / "scene.json")["splat"]["status"] == "trained"
    assert discover(vault, jobs) == [] and run_next(vault, jobs, "gpu", "gpu", idle_s=3600, now=day, **quiet) is None


def test_one_scan_is_made_by_one_process_at_a_time(tmp_path: Path) -> None:
    from ingest.splat.run import Busy, exclusive

    with exclusive(tmp_path / "work"), pytest.raises(Busy), exclusive(tmp_path / "work"):
        pass
    with exclusive(tmp_path / "work"):  # released
        pass

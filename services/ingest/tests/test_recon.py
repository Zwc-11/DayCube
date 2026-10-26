"""Walks rebuilt from images alone (ingest/recon): a curved walk between two walls, ray-cast with
a texture that never repeats (so SIFT has features to match), with the phone's poses written as
Stray Scanner writes them and a GPS track 3 s off the video's clock. Images alone must rebuild the
walk, gravity and the GPS must make it metric, and the training sets must carry the held-out
frames into the reconstruction's frame."""

from __future__ import annotations

import json
import math
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from ingest.phone.geo import EARTH_M
from ingest.recon import align, learned, sfm, simulate
from ingest.skeleton.parsers.types import Fix

pytestmark = [
    pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs FFmpeg"),
    pytest.mark.skipif(not sfm.available(), reason="needs pycolmap"),
]

W, H, F = 640, 480, 480.0
FPS = 5
SECONDS = 24.0
SPEED = 1.0  # m/s
GROUND_Y, RIGHT_X, LEFT_X = -1.5, 3.0, -3.5
START = datetime(2026, 10, 24, 20, 28, tzinfo=UTC)
LAT0, LON0 = 43.70, -79.40
NORTH_DEG = 30.0  # the walk's -z axis points 30 degrees east of north


def _pose(t: float) -> tuple[np.ndarray, np.ndarray]:
    """Position and OpenCV camera-to-world rotation at time t: along -z, swaying 0.8 m side to side,
    looking along the path."""
    z = -SPEED * t
    x = 0.8 * math.sin(t / 3.0)
    dx = 0.8 / 3.0 * math.cos(t / 3.0)
    forward = np.array([dx, 0.0, -SPEED])
    forward /= np.linalg.norm(forward)
    down = np.array([0.0, -1.0, 0.0])
    right = np.cross(down, forward)
    return np.array([x, 0.0, z]), np.stack([right, down, forward], 1)


def _hash(i: np.ndarray, j: np.ndarray, seed: float) -> np.ndarray:
    return np.modf(np.abs(np.sin(i * 12.9898 + j * 78.233 + seed) * 43758.5453))[0]


def _noise(u: np.ndarray, v: np.ndarray, seed: float) -> np.ndarray:
    """Value noise in [0, 1] over three scales: blobs SIFT can find, and none alike."""
    out = np.zeros_like(u)
    for cell, weight in ((1.2, 0.4), (0.4, 0.38), (0.13, 0.22)):
        x, y = u / cell, v / cell
        i, j = np.floor(x), np.floor(y)
        fx, fy = x - i, y - j
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        a, b = _hash(i, j, seed), _hash(i + 1, j, seed)
        c, d = _hash(i, j + 1, seed), _hash(i + 1, j + 1, seed)
        out += weight * ((a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy)
    return out


def _render(position: np.ndarray, R: np.ndarray, ss: int = 2) -> np.ndarray:
    """The frame, supersampled ``ss`` times each way (distant texture would shimmer otherwise)."""
    u, v = np.meshgrid((np.arange(W * ss) + 0.5) / ss, (np.arange(H * ss) + 0.5) / ss)
    rays = np.stack([(u - W / 2) / F, (v - H / 2) / F, np.ones_like(u)], -1) @ R.T
    inf = np.full(u.shape, np.inf)
    with np.errstate(divide="ignore", invalid="ignore"):
        hits = [
            np.where(rays[..., 1] < -1e-6, (GROUND_Y - position[1]) / rays[..., 1], inf),
            np.where(rays[..., 0] > 1e-6, (RIGHT_X - position[0]) / rays[..., 0], inf),
            np.where(rays[..., 0] < -1e-6, (LEFT_X - position[0]) / rays[..., 0], inf),
        ]
    which = np.argmin(np.stack(hits), 0)
    t = np.min(np.stack(hits), 0)
    p = position + rays * np.where(np.isfinite(t), t, 0)[..., None]
    a = np.where(which == 0, p[..., 0], p[..., 2])
    b = np.where(which == 0, p[..., 2], p[..., 1])
    seed = which * 3.7
    # Brightness carries the pattern (SIFT sees grey); each surface has its own tint.
    light = _noise(a, b, seed + 1)
    tint = np.array([[1.0, 0.85, 0.7], [0.75, 0.9, 1.0], [0.9, 1.0, 0.8]])[which]
    rgb = (light[..., None] * 1.6 - 0.3).clip(0, 1) * tint * 240 + 8
    far = ~np.isfinite(t) | (t > 40)
    rgb[far] = [150, 185, 225]
    rgb = rgb.reshape(H, ss, W, ss, 3).mean((1, 3))
    return rgb.clip(0, 255).astype(np.uint8)


def _quat(R: np.ndarray) -> tuple[float, float, float, float]:
    """x, y, z, w of a rotation matrix (Shepperd: from its largest diagonal term)."""
    trace = R[0, 0] + R[1, 1] + R[2, 2]
    if trace > 0:
        s = 2 * math.sqrt(1 + trace)
        return ((R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, s / 4)
    i = int(np.argmax(np.diag(R)))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = 2 * math.sqrt(1 + R[i, i] - R[j, j] - R[k, k])
    q = [0.0, 0.0, 0.0, (R[k, j] - R[j, k]) / s]
    q[i], q[j], q[k] = s / 4, (R[j, i] + R[i, j]) / s, (R[k, i] + R[i, k]) / s
    return q[0], q[1], q[2], q[3]


def make_walk(folder: Path) -> Path:
    folder.mkdir(parents=True)
    n = int(SECONDS * FPS)
    frames = np.stack([_render(*_pose(k / FPS)) for k in range(n)])
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "pipe:0", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "14",
         "-metadata", f"creation_time={START.strftime('%Y-%m-%dT%H:%M:%S.000000Z')}", str(folder / "rgb.mp4")],
        input=frames.tobytes(), check=True,
    )  # fmt: skip
    rows = ["timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy"]
    for k in range(n):
        p, R = _pose(k / FPS)
        q = _quat(R)
        rows.append(f"{500 + k / FPS:.4f}, {k:06d}, {p[0]:.5f}, {p[1]:.5f}, {p[2]:.5f}, "
                    f"{q[0]:.7f}, {q[1]:.7f}, {q[2]:.7f}, {q[3]:.7f}, {F}, {F}, {W / 2}, {H / 2}")  # fmt: skip
    (folder / "odometry.csv").write_text("\n".join(rows) + "\n")
    return folder


def gps(noise_m: float = 0.3) -> list[Fix]:
    """The walk's GPS at 1 Hz from 30 s before to 30 s after, a little noisy."""
    rng = np.random.default_rng(1)
    a = math.radians(NORTH_DEG)
    fixes = []
    for k in range(-30, int(SECONDS) + 31):
        t = min(max(float(k), 0.0), SECONDS)
        p, _ = _pose(t)
        x, fwd = p[0], -p[2]  # right of the walk, along it
        east = fwd * math.sin(a) + x * math.cos(a) + rng.normal(0, noise_m)
        north = fwd * math.cos(a) - x * math.sin(a) + rng.normal(0, noise_m)
        lat = LAT0 + math.degrees(north / EARTH_M)
        lon = LON0 + math.degrees(east / (EARTH_M * math.cos(math.radians(LAT0))))
        fixes.append(Fix(t=START + timedelta(seconds=k), lat=lat, lon=lon))
    return fixes


@pytest.fixture(scope="module")
def walk(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_walk(tmp_path_factory.mktemp("recon") / "scan")


def test_a_similarity_is_recovered_and_gravity_levels() -> None:
    rng = np.random.default_rng(0)
    src = rng.normal(size=(50, 3))
    a = math.radians(40)
    R = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    dst = 2.5 * src @ R.T + np.array([1.0, -2.0, 3.0])
    s, R2, t = align.umeyama(src, dst)
    assert s == pytest.approx(2.5)
    assert np.allclose(R2, R) and np.allclose(t, [1.0, -2.0, 3.0])
    # A reconstruction tipped 20 degrees: the cameras' gravity turns it back upright.
    tip = math.radians(20)
    T = np.array([[1, 0, 0], [0, math.cos(tip), -math.sin(tip)], [0, math.sin(tip), math.cos(tip)]])
    c2w = np.tile(np.eye(4), (5, 1, 1))
    c2w[:, :3, :3] = T  # every camera upright in the true frame, the model tipped
    down_cam = np.tile([0.0, 1.0, 0.0], (5, 1))  # OpenCV: down is +y in an upright camera
    L = align.level(c2w, down_cam)
    assert np.allclose(L @ T @ np.array([0.0, 1.0, 0.0]), [0.0, -1.0, 0.0], atol=1e-9)


def test_the_gps_fit_finds_the_clock_offset_and_not_the_gps_standing_still() -> None:
    """The walk's path at half scale against the GPS, which stands still for 30 s before and after
    it: with a free scale, an offset onto a still stretch would fit a point with no residual."""
    t = np.arange(0.5, SECONDS, 1.0)
    path = np.array([_pose(x)[0] for x in t]) * 0.5
    for late in (3, 0, -5):
        fit = align.georeference(path, t, START.timestamp() + late, gps())
        assert fit["offset_s"] == -late and fit["scale"] == pytest.approx(2.0, rel=0.02) and fit["rmse_m"] < 0.5


def test_frames_are_picked_as_glasses_would_take_them() -> None:
    t = np.arange(0, 20, 1 / 30)
    photos = simulate.every(t, 3.0)
    assert np.allclose(t[photos], [0.5, 3.5, 6.5, 9.5, 12.5, 15.5, 18.5], atol=1 / 30)
    held = simulate.held_out(t, 6, avoid=[photos, simulate.every(t, 1.0)])
    assert len(held) == 6 and not set(held) & set(photos) and not set(held) & set(simulate.every(t, 1.0))
    # Stills are sharp: the sharpest video frame within 0.15 s of each photo's time.
    sharp = np.ones(len(t))
    sharp[[18, 108]] = 9.0  # 0.6 s and 3.6 s; 3.7 s (111) is past the window
    sharp[111] = 99.0
    assert list(simulate.every(t, 3.0, sharp=sharp)[:2]) == [18, 108]


def test_images_alone_rebuild_the_walk_and_the_gps_makes_it_metric(walk: Path, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.setitem(simulate.MODES, "photo_2s", {"every_s": 2.0, "long_side": 1920, "label": "a photo every 2 s"})
    monkeypatch.setitem(simulate.MODES, "stream", {"every_s": 0.4, "long_side": 1920, "label": "a stream"})
    seen = []
    # The video's clock is 3 s late: the fit must find it. Photos 2 m apart along a walk looking
    # ahead: COLMAP's defaults refuse every first pair (forward motion), so none were placed.
    report = simulate.simulate(walk, walk / "rgb.mp4", tmp_path / "work", fixes=gps(), t0=START.timestamp() + 3,
                               modes=["photo_2s", "stream"], on_result=seen.append, say=lambda _: None)  # fmt: skip
    assert len(seen) == 2 and report["path_m"] == pytest.approx(24.5, abs=0.5)
    for name, r in report["modes"].items():
        assert r["registered_share"] >= 0.9, name
        assert r["path"]["rmse_m"] < 0.1, (name, r["path"])
        assert r["path"]["turn_median_deg"] < 1.0, (name, r["path"])
        # Twelve photos 2 s apart pin the clock to a second; the stream to the second.
        assert abs(r["gps"]["offset_s"] + 3) <= (1 if name == "photo_2s" else 0), (name, r["gps"])
        assert r["gps"]["rmse_m"] < 1.0 and abs(r["gps"]["scale_error_pct"]) < 4.0, (name, r["gps"])
        assert report["checks"][name] == {"placed_90pct": True, "path_rmse_le_0_5m": True, "gps_rmse_le_3m": True,
                                          "scale_within_5pct": True}  # fmt: skip
    assert (tmp_path / "work" / "stream" / "colmap" / "sparse" / "images.txt").is_file()


def test_the_training_sets_carry_the_held_out_frames_into_each_reconstruction(walk: Path, tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.setitem(simulate.MODES, "stream", {"every_s": 0.4, "long_side": 1920, "label": "a stream"})
    monkeypatch.setattr(simulate, "HELD_OUT", 8)
    trained = {}

    def trainer(folder: Path, export: Path, *, steps: int, say: object) -> dict:
        frames = json.loads((folder / "transforms.json").read_text())["frames"]
        held = json.loads((folder / "transforms_val.json").read_text())["frames"]
        trained[folder.name] = (frames, held)
        assert (folder / "init.ply").is_file() and all((folder / f["file_path"]).is_file() for f in frames + held)
        return {"psnr_db": 25.0 if folder.name == "train_phone" else 24.0, "steps": steps}

    report = simulate.simulate(walk, walk / "rgb.mp4", tmp_path / "work", fixes=gps(), t0=START.timestamp(),
                               modes=["stream"], train=True, steps=123, trainer=trainer, say=lambda _: None)  # fmt: skip
    r = report["modes"]["stream"]
    assert r["train"]["psnr_cost_db"] == 1.0 and r["train"]["recon"]["steps"] == 123
    pose = {name: (np.array([f["transform_matrix"] for f in frames]), np.array([f["transform_matrix"] for f in held]))
            for name, (frames, held) in trained.items()}  # fmt: skip
    assert len(pose["train_recon"][1]) == 8
    # In metres both ways (the GPS scale): the walk's steps are the same length, and each held-out
    # camera stands where it does among the training cameras.
    for name in ("train_recon", "train_phone"):
        steps = np.linalg.norm(np.diff(pose[name][0][:, :3, 3], axis=0), axis=1)
        assert steps.mean() == pytest.approx(0.4 * SPEED, rel=0.05), name

    def nearest(train: np.ndarray, held: np.ndarray) -> np.ndarray:
        d = np.linalg.norm(held[:, None, :3, 3] - train[None, :, :3, 3], axis=2)
        return d.min(1)

    assert np.allclose(nearest(*pose["train_recon"]), nearest(*pose["train_phone"]), atol=0.05)
    # Upright in the reconstruction too: the cameras' up (OpenGL +y) is world +y.
    ups = pose["train_recon"][0][:, :3, 1]
    assert np.all(ups[:, 1] > 0.99)


def test_sequential_pairs_are_the_next_few_and_quadratically_further() -> None:
    pairs = learned.sequential_pairs(12, 2)
    assert (0, 1) in pairs and (0, 2) in pairs and (0, 3) in pairs and (0, 4) in pairs and (0, 6) in pairs
    assert (0, 5) not in pairs and (0, 10) in pairs and all(a < b < 12 for a, b in pairs)


@pytest.mark.skipif(not learned.available(), reason="needs PyTorch and Kornia (services/recon)")
def test_learned_features_rebuild_photos_2_m_apart(walk: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(simulate.MODES, "photo_2s", {"every_s": 2.0, "long_side": 1920, "label": "a photo every 2 s"})
    report = simulate.simulate(walk, walk / "rgb.mp4", tmp_path / "work", fixes=gps(), t0=START.timestamp(),
                               modes=["photo_2s"], features="aliked", say=lambda _: None)  # fmt: skip
    r = report["modes"]["photo_2s"]
    assert report["features"] == "aliked" and r["registered_share"] >= 0.9, r
    assert r["path"]["rmse_m"] < 0.15 and r["path"]["turn_median_deg"] < 1.5, r["path"]
    assert r["timings"]["matched_pairs"] >= r["images"] - 1

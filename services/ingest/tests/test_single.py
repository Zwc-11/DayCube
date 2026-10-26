"""One photo (ingest/recon/single): lifted to 3D alone, and located in a scan. The walk of
test_recon (ray-cast between two walls), now with its true depth written as Stray Scanner writes
the LiDAR's, so the lift with that depth must render the walk ahead almost as the camera saw it,
a depth model off by a scale must be measured as off, and a photo must land in the scan where the
camera stood."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from ingest.recon import learned, single
from ingest.splat import dataset
from PIL import Image
from test_recon import FPS, GROUND_Y, LEFT_X, RIGHT_X, START, F, H, W, _pose, gps, make_walk

pytestmark = pytest.mark.skipif(not learned.available(), reason="needs PyTorch (services/recon)")


def _depth(position: np.ndarray, R: np.ndarray, width: int, height: int) -> np.ndarray:
    """Depth along the camera's axis (m) of each pixel of a ``width`` x ``height`` image of the
    frame: 0 where nothing is hit (the sky)."""
    s = width / W
    u, v = np.meshgrid((np.arange(width) + 0.5) / s, (np.arange(height) + 0.5) / s)
    rays = np.stack([(u - W / 2) / F, (v - H / 2) / F, np.ones_like(u)], -1) @ R.T
    inf = np.full(u.shape, np.inf)
    with np.errstate(divide="ignore", invalid="ignore"):
        hits = np.stack([
            np.where(rays[..., 1] < -1e-6, (GROUND_Y - position[1]) / rays[..., 1], inf),
            np.where(rays[..., 0] > 1e-6, (RIGHT_X - position[0]) / rays[..., 0], inf),
            np.where(rays[..., 0] < -1e-6, (LEFT_X - position[0]) / rays[..., 0], inf),
        ])  # fmt: skip
    t = hits.min(0)  # a ray's camera z is 1, so its length to the hit is the depth
    return np.where(np.isfinite(t) & (t < 40), t, 0.0)


@pytest.fixture(scope="module")
def scan(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The walk with LiDAR-like depth (256 x 192, mm) and confidence for every frame."""
    folder = make_walk(tmp_path_factory.mktemp("single") / "scan")
    (folder / "depth").mkdir()
    (folder / "confidence").mkdir()
    n = len((folder / "odometry.csv").read_text().splitlines()) - 1
    for k in range(n):
        d = _depth(*_pose(k / FPS), dataset.DEPTH_W, dataset.DEPTH_H)
        Image.fromarray(np.round(d * 1000).astype(np.uint16)).save(folder / "depth" / f"{k:06d}.png")
        Image.fromarray(np.where(d > 0, 2, 0).astype(np.uint8)).save(folder / "confidence" / f"{k:06d}.png")
    return folder


class ScaledDepth:
    """A depth model that knows the walk's true depth, off by ``scale``: it finds which frame it was
    given by comparing it with the decoded video."""

    def __init__(self, video: Path, scale: float) -> None:
        n = dataset._video_size(video)[2]
        self.frames, _ = dataset.frames_at(video, np.arange(n), (W, H))
        self.scale = scale

    def __call__(self, rgb: np.ndarray, K: np.ndarray) -> np.ndarray:
        k = int(np.argmin(np.abs(self.frames[:, ::8, ::8].astype(int) - rgb[::8, ::8].astype(int)).mean((1, 2, 3))))
        return _depth(*_pose(k / FPS), rgb.shape[1], rgb.shape[0]) * self.scale


def test_a_plane_drawn_from_its_own_camera_is_itself() -> None:
    rng = np.random.default_rng(0)
    rgb = rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)
    K = np.array([50.0, 50.0, 31.5, 23.5])
    c2w = np.eye(4)
    cloud = single.lift(rgb, np.full((48, 64), 3.0), K, c2w)
    image, covered = single.render(cloud, c2w, K, (64, 48))
    assert covered.all() and np.array_equal(image, rgb)
    # A step forward: everything still covered (the view is inside the photo), each point bigger.
    ahead = np.eye(4)
    ahead[2, 3] = 1.0
    image, covered = single.render(cloud, ahead, K, (64, 48))
    assert covered.mean() > 0.99


def test_the_lidar_lift_renders_the_walk_ahead_and_a_wrong_scale_is_measured(scan: Path, tmp_path: Path) -> None:
    report = single.run(scan, scan / "rgb.mp4", tmp_path / "work", photos=3, methods=("flat", "lidar", "depth-pro"),
                        locate_too=False, models={"depth-pro": ScaledDepth(scan / "rgb.mp4", 1.3)}, say=lambda _: None)  # fmt: skip
    assert len(report["photos"]) == 3 and all(p["lidar"] for p in report["photos"])
    lidar, flat, scaled = (report["lift"][m] for m in ("lidar", "flat", "depth-pro"))
    for d in ("0.25", "0.5", "1"):
        assert lidar["ahead"][d]["coverage"] > 0.9
        assert lidar["ahead"][d]["psnr"] > lidar["ahead"][d]["psnr_flat"] + 4
    assert lidar["ahead"]["1"]["psnr"] > 22
    assert flat["ahead"]["1"]["coverage"] == 1.0
    # 30% too deep: measured against the LiDAR, and the render is worse for it.
    assert scaled["depth_vs_lidar"]["scale"] == pytest.approx(1.3, abs=0.03)
    assert scaled["depth_vs_lidar"]["delta_1_25"] < 0.2
    assert scaled["ahead"]["1"]["psnr"] < lidar["ahead"]["1"]["psnr"] - 2
    assert list((tmp_path / "work" / "lift").glob("*_lidar_1m.jpg"))


def test_a_photo_is_located_in_the_scan_where_the_camera_stood(scan: Path, tmp_path: Path) -> None:
    # The GPS is 0.3 m noisy and the video's clock 3 s late, as in test_recon: the prior comes from
    # fitting the scan's path to it, and the photo's own scan frames (within 1 s) are left out.
    report = single.run(scan, scan / "rgb.mp4", tmp_path / "work", photos=4, methods=(), locate_too=True,
                        fixes=gps(), t0=START.timestamp() + 3, say=lambda _: None)  # fmt: skip
    located = report["locate"]
    assert located["prior"] == "gps" and located["gps_fit"]["offset_s"] == -3
    assert located["placed"] == located["photos"] == 4
    assert located["median_position_m"] < 0.1 and located["median_rotation_deg"] < 1.0
    assert located["within"]["0.25m_2deg"] == 1.0
    for row in located["per_photo"]:
        assert row["candidates"] > 0 and row["inliers"] >= 30 and row["prior_error_m"] < 2
        # The scan drawn from the pose found is as good as from the true pose.
        assert row["psnr_scan"] > row["psnr_scan_at_truth"] - 1.5
    assert len(list((tmp_path / "work" / "locate").glob("*.jpg"))) == 4


def test_pose_errors() -> None:
    a = np.eye(4)
    b = np.eye(4)
    b[:3, 3] = [0.3, 0.4, 0.0]
    angle = math.radians(10)
    b[:3, :3] = [[math.cos(angle), -math.sin(angle), 0], [math.sin(angle), math.cos(angle), 0], [0, 0, 1]]
    dp, dr = single.pose_error(a, b)
    assert dp == pytest.approx(0.5) and dr == pytest.approx(10.0)

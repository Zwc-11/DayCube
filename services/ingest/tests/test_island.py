"""A shot as an island of the world (ingest/recon/island.py): the geometry, without the models."""

from __future__ import annotations

import math

import numpy as np
import pytest
from ingest.recon import island
from ingest.world.geo import local_from_lat_lon


def street(rng: np.random.Generator) -> np.ndarray:
    """A camera 1.45 m above a sidewalk with a wall 6 m to its right (island axes)."""
    ground = np.stack([rng.uniform(-5, 5, 4000), np.zeros(4000), rng.uniform(-30, -1, 4000)], 1)
    wall = np.stack([np.full(4000, 6.0), rng.uniform(0, 14, 4000), rng.uniform(-30, -1, 4000)], 1)
    return np.concatenate([ground, wall]) + rng.normal(0, 0.01, (8000, 3))


def test_the_ground_sets_the_scale_and_up() -> None:
    rng = np.random.default_rng(0)
    pts = street(rng)
    # Seen by a camera tilted 10 degrees down, its depths 2.6 times too long (as on the dusk test).
    tilt = math.radians(10)
    to_cam = np.array([[1, 0, 0], [0, -math.cos(tilt), math.sin(tilt)], [0, -math.sin(tilt), -math.cos(tilt)]])
    cam = ((pts - [0, island.CAMERA_M, 0]) @ to_cam.T) * 2.6
    scale, down = island.ground_ruler(cam)
    assert scale == pytest.approx(1 / 2.6, rel=0.02)
    R = island.upright(down)
    back = (cam * scale) @ R.T + [0, island.CAMERA_M, 0]
    assert np.median(np.abs(back[:4000, 1])) < 0.03 and np.abs(back[:4000, 1]).max() < 0.12  # the ground at y = 0
    normal, distance = island.facade(back)
    assert distance == pytest.approx(6.0, rel=0.03)  # within the ruler's 2%
    assert normal @ [-1, 0, 0] > 0.99  # facing the camera, on its left


def test_people_set_the_scale_as_adults() -> None:
    f = 1000.0
    # Three people 1.70 m tall at 4, 6 and 9 m, their depths read 2.5 times too long.
    assert island.people_ruler([f * 1.7 / d for d in (4, 6, 9)], [2.5 * d for d in (4, 6, 9)], f) == pytest.approx(0.4)
    assert island.people_ruler([], [], f) is None


def test_one_view_keeps_the_street_and_drops_the_sky_and_the_far_side() -> None:
    pts = np.array([[2, 1, -10], [2, 1, -40], [3, 20, -10], [-3, 10, -12], [-3, 0.2, -12], [2, 8, -20]], float)
    rgb = np.array([[150, 140, 120], [150, 140, 120], [150, 140, 120], [40, 40, 50], [90, 90, 90], [110, 140, 190]])
    assert island.crop(pts, rgb).tolist() == [True, False, False, False, True, False]


def test_an_island_is_laid_on_its_wall() -> None:
    # A wall running east from a corner; the camera walked east with it on its right (south).
    a = (43.6452, -79.3820)
    b = (43.6452, -79.3800)
    normal = np.array([-1.0, 0.0, 0.0])  # the facade, seen on the right, faces the camera's left
    lat, lon, R = island.place_on_wall(a, b, along=12.0, normal=normal, distance=3.0)
    x, z = local_from_lat_lon(*a, lat, lon)
    assert x == pytest.approx(12.0, abs=0.05) and z == pytest.approx(-3.0, abs=0.05)  # 3 m north of the wall
    assert R @ normal == pytest.approx([0, 0, -1], abs=1e-9)  # facing north, away from the wall
    assert np.allclose(R @ [0, 0, -1], [1, 0, 0])  # walking east
    assert np.linalg.det(R) == pytest.approx(1)


def test_records_are_the_viewers_splats() -> None:
    data = island.splat_records(np.array([[1, 2, 3]], np.float32), np.array([[10, 20, 30]], np.uint8), np.array([0.05]))
    assert len(data) == 32
    assert np.frombuffer(data[:12], "<f4").tolist() == [1, 2, 3]
    assert list(data[24:32]) == [10, 20, 30, 255, 255, 128, 128, 128]

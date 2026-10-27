"""Memories (ingest/recon/memory): a photo made into Gaussians, written as the viewer's splat
records in its axes, kept in the day it was taken, and served by the API. SHARP itself needs
PyTorch and its weights; a stand-in predicts a known scene here."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from ingest.recon import memory
from ingest.splat import export
from ingest.vault import Vault
from PIL import Image


def rotation(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])  # fmt: skip


def plane(rgb: np.ndarray, f_px: float, depth: float = 3.0) -> dict:
    """A stand-in for SHARP: one Gaussian per 8th pixel on a plane ``depth`` m away, coloured from
    the photo, in OpenCV camera axes (y down, z forward)."""
    h, w = rgb.shape[:2]
    v, u = np.mgrid[0:h:8, 0:w:8]
    z = np.full(u.shape, depth)
    means = np.stack([(u - w / 2) / f_px * z, (v - h / 2) / f_px * z, z], -1).reshape(-1, 3)
    n = len(means)
    return {"means": means, "scales": np.full((n, 3), 4 * depth / f_px), "quats": np.tile([1.0, 0, 0, 0], (n, 1)),
            "rgb": rgb[::8, ::8].reshape(-1, 3) / 255.0, "alpha": np.full(n, 0.9)}  # fmt: skip


def photo(path: Path, when: datetime, focal_35mm: int = 26) -> Path:
    """A JPEG as a phone writes one: EXIF time and 35 mm-equivalent focal length."""
    rng = np.random.default_rng(0)
    image = Image.fromarray(rng.integers(0, 255, (300, 400, 3), dtype=np.uint8))
    exif = Image.Exif()
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")  # DateTimeOriginal
    sub[0xA405] = focal_35mm  # FocalLengthIn35mmFilm
    image.save(path, exif=exif)
    return path


def test_the_photos_camera_axes_become_the_viewers() -> None:
    rng = np.random.default_rng(3)
    q = rng.normal(size=(5, 4))
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    p = rng.normal(size=(5, 3)) + [0, 0, 5]
    p2, q2 = memory.to_viewer(p, q)
    F = np.diag(memory.FLIP)
    assert np.allclose(p2, p @ F)
    for a, b in zip(q, q2, strict=True):
        # The Gaussian's shape turns with the axes: F R (its rotation seen in the new axes).
        assert np.allclose(rotation(b), F @ rotation(a))
    # In front of the camera (OpenCV +z) is down the viewer's -z; below (+y) is the viewer's -y.
    p3, _ = memory.to_viewer(np.array([[0.0, 1.0, 2.0]]), np.array([[1.0, 0, 0, 0]]))
    assert np.allclose(p3, [[0.0, -1.0, -2.0]])


def test_a_photo_becomes_a_memory(tmp_path: Path) -> None:
    rgb = np.random.default_rng(1).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    meta = memory.make(rgb, 500.0, tmp_path / "m", predict=lambda image, f: plane(image, f), source={"file": "x.jpg"})
    records = np.frombuffer((tmp_path / "m" / "memory.splat").read_bytes(), export.RECORD)
    assert meta["splats"] == len(records) == (480 // 8) * (640 // 8)
    assert np.allclose(records["position"][:, 2], -3.0)  # in front: down -z
    assert meta["depth_m"]["median"] == pytest.approx(3.0)
    assert meta["fov_y_deg"] == pytest.approx(2 * np.degrees(np.arctan(240 / 500)), abs=0.01)
    assert Image.open(tmp_path / "m" / "photo.jpg").size == (640, 480)
    assert json.loads((tmp_path / "m" / "memory.json").read_text())["source"] == {"file": "x.jpg"}


def test_inbox_photos_go_to_the_day_they_were_taken(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    vault.initialize()
    inbox = vault.root / "inbox" / "memories"
    inbox.mkdir(parents=True)
    photo(inbox / "IMG_0001.jpg", datetime(2026, 10, 25, 14, 30))
    rgb, f_px, when = memory.load_photo(inbox / "IMG_0001.jpg")
    assert when == datetime(2026, 10, 25, 14, 30)
    assert f_px == pytest.approx(26 / 36 * 400)  # 26 mm-equivalent across 400 px
    made = memory.from_inbox(vault, lambda image, f: plane(image, f), say=lambda _: None)
    assert len(made) == 1 and made[0]["date"] == "2026-10-25"
    folder = vault.root / "2026-10-25" / "memories" / made[0]["id"]
    assert (folder / "memory.splat").is_file() and (folder / "original.jpg").is_file()
    assert not list(inbox.iterdir())  # moved in, so it is not made again


def test_the_api_lists_and_serves_memories(client: TestClient, vault: Vault, tmp_path: Path) -> None:
    rgb = np.random.default_rng(2).integers(0, 255, (240, 320, 3), dtype=np.uint8)
    ident = memory.memory_id(b"one")
    memory.make(rgb, 300.0, vault.root / "2026-10-20" / "memories" / ident, predict=lambda image, f: plane(image, f))
    listed = client.get("/memories").json()
    assert [(m["date"], m["id"]) for m in listed] == [("2026-10-20", ident)]
    assert listed[0]["splats"] == (240 // 8) * (320 // 8) and listed[0]["depth_m"]["median"] == pytest.approx(3.0)
    splat = client.get(f"/memories/2026-10-20/{ident}/memory.splat")
    assert splat.status_code == 200 and len(splat.content) == listed[0]["splats"] * 32
    assert client.get(f"/memories/2026-10-20/{ident}/photo.jpg").headers["content-type"] == "image/jpeg"
    for bad in (f"/memories/2026-10-20/{ident}/memory.json", "/memories/2026-10-20/mem_..%2F..%2Fx/photo.jpg",
                "/memories/2026-10-20/mem_000000000000/photo.jpg"):  # fmt: skip
        assert client.get(bad).status_code == 404

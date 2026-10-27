"""Memories (ingest/recon/memory): a photo made into Gaussians, written as the viewer's splat
records in its axes, kept in the day it was taken, and served by the API. SHARP itself needs
PyTorch and its weights; a stand-in predicts a known scene here."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from capture import memories as maker
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


def photo(path: Path, when: datetime, focal_35mm: int = 26, age_s: float = 60.0) -> Path:
    """A JPEG as a phone writes one: EXIF time and 35 mm-equivalent focal length; copied in
    ``age_s`` ago."""
    rng = np.random.default_rng(0)
    image = Image.fromarray(rng.integers(0, 255, (300, 400, 3), dtype=np.uint8))
    exif = Image.Exif()
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")  # DateTimeOriginal
    sub[0xA405] = focal_35mm  # FocalLengthIn35mmFilm
    image.save(path, exif=exif)
    then = time.time() - age_s
    os.utime(path, (then, then))
    return path


def scan(vault: Vault, date: str, moment: str, *, capture: bool) -> None:
    """A scan moment; with its capture (poses and depth) beside it, or with the capture gone."""
    folder = vault.root / date / "moments" / moment
    folder.mkdir(parents=True)
    (folder / "scene.json").write_text(json.dumps({"kind": "camera-path"}))
    if capture:
        (folder / "capture" / "depth").mkdir(parents=True)
        (folder / "capture" / "odometry.csv").write_text("timestamp\n")


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
    photo(inbox / "IMG_0002.jpg", datetime(2026, 10, 25, 15, 0), age_s=0)  # still being copied in
    (inbox / "IMG_0003.jpg").write_bytes(b"not a photo")
    os.utime(inbox / "IMG_0003.jpg", (time.time() - 60, time.time() - 60))
    made = memory.from_inbox(vault, lambda image, f: plane(image, f), say=lambda _: None)
    assert len(made) == 1 and made[0]["date"] == "2026-10-25"
    folder = vault.root / "2026-10-25" / "memories" / made[0]["id"]
    assert (folder / "memory.splat").is_file() and (folder / "original.jpg").is_file()
    # Moved in, so it is not made again; the one being copied waits; the unreadable one is set aside.
    assert sorted(p.name for p in inbox.iterdir()) == ["IMG_0002.jpg", "failed"]
    assert sorted(p.name for p in (inbox / "failed").iterdir()) == ["IMG_0003.jpg", "IMG_0003.jpg.txt"]


def test_sharp_is_loaded_only_for_a_photo_to_make() -> None:
    loads = []
    lazy = memory.Lazy(lambda: loads.append(1) or (lambda image, f: plane(image, f)))
    assert loads == []
    lazy(np.zeros((16, 16, 3), np.uint8), 10.0)
    lazy(np.zeros((16, 16, 3), np.uint8), 10.0)
    assert loads == [1]


def test_the_latest_scan_is_the_newest_with_its_capture(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    vault.initialize()
    assert memory.latest_scan(vault) is None
    scan(vault, "2026-10-20", "mo_scan_a", capture=True)
    scan(vault, "2026-10-24", "mo_scan_b", capture=True)
    scan(vault, "2026-10-26", "mo_scan_c", capture=False)  # its capture deleted: nothing to make it from
    assert memory.latest_scan(vault) == "2026-10-24"


def test_the_receiver_makes_memories_without_a_click(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = Vault(tmp_path / "vault")
    vault.initialize()
    runs: list[list[str]] = []

    class Child:
        def __init__(self, command: list[str], **kw: object) -> None:
            runs.append(command)
            self.code = codes.pop(0)

        def wait(self, timeout: float | None = None) -> int:
            return self.code

        def poll(self) -> int:
            return self.code

    codes = [0, 1, 0]
    monkeypatch.setattr(maker.subprocess, "Popen", Child)
    m = maker.MemoryMaker(vault.root, Path("python"))
    assert m.step() is None  # nothing to make
    # A scan and no memories: the scan's frames, once per start.
    scan(vault, "2026-10-24", "mo_scan_b", capture=True)
    assert m.step() == 0 and runs[-1][1:] == [
        "-m",
        "ingest.recon.memory",
        "--scan",
        "latest",
        "--count",
        "6",
        "--if-none",
    ]
    assert m.step() is None
    # A photo being copied in waits; once it has settled, it is made.
    inbox = vault.root / "inbox" / "memories"
    inbox.mkdir(parents=True)
    photo(inbox / "IMG_0001.jpg", datetime(2026, 10, 25, 14, 30), age_s=0)
    assert m.step() is None
    now = time.time() + maker.SETTLED_S + 1
    assert m.step(now) == 1 and runs[-1][3:] == ["--inbox"]
    # It failed: not tried again every minute, but after the back-off.
    assert m.step(now + 60) is None
    assert m.step(now + maker.BACKOFF_S + 1) == 0 and len(runs) == 3
    assert "receiver: -m ingest.recon.memory --inbox" in (vault.root / "logs" / "memories.log").read_text()


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


def test_the_lidar_sets_a_memorys_scale_in_its_scan() -> None:
    # SHARP put a wall 2 m away; the LiDAR measured it at 4 m: the scan's metres are twice SHARP's.
    rgb = np.zeros((480, 640, 3), np.uint8)
    g = plane(rgb, 500.0, depth=2.0)
    lidar = np.full((192, 256), 4.0, np.float32)
    confidence = np.full((192, 256), 2, np.uint8)
    assert memory.lidar_scale(
        g["means"], np.array([500.0, 500.0, 319.5, 239.5]), 640, lidar, confidence
    ) == pytest.approx(2.0)
    confidence[:] = 0  # nothing reliable: no scale
    assert memory.lidar_scale(g["means"], np.array([500.0, 500.0, 319.5, 239.5]), 640, lidar, confidence) is None


def test_a_memory_from_a_scan_lists_its_world(client: TestClient, vault: Vault) -> None:
    rgb = np.random.default_rng(4).integers(0, 255, (240, 320, 3), dtype=np.uint8)
    pose = np.eye(4).tolist()
    moment = vault.root / "2026-10-20" / "moments" / "mo_scan_test"
    moment.mkdir(parents=True)
    (moment / "scene.json").write_text(json.dumps({"splat": {"trained": "splat/scene.splat", "lidar": "splat/lidar.splat",
                                                              "bounds": None}}))  # fmt: skip
    for name, world in (("placed", "mo_scan_test"), ("escaping", "../2026-10-20")):
        memory.make(rgb, 300.0, vault.root / "2026-10-20" / "memories" / memory.memory_id(name.encode()),
                    predict=lambda image, f: plane(image, f),
                    world=lambda g, world=world: {"moment": world, "pose": pose, "scale": 2.0, "scale_from": "lidar"})  # fmt: skip
    listed = {m["id"]: m for m in client.get("/memories").json()}
    placed = listed[memory.memory_id(b"placed")]["world"]
    assert placed["splat"] == "moments/mo_scan_test/splat/scene.splat" and placed["trained"] is True
    assert placed["scale"] == 2.0 and placed["pose"] == pose
    assert "world" not in listed[memory.memory_id(b"escaping")]  # a moment name that leaves its day is ignored


def test_a_video_shorter_than_its_poses_still_gives_its_sharpest_frames() -> None:
    # The trail's video decoded to 3861 frames, its poses one more: the last pose has no score.
    from ingest.splat import dataset

    scores = np.arange(10, dtype=float)
    picks = dataset.choose(scores, 3, np.ones(11, bool))
    assert len(picks) == 3 and picks.max() == 9

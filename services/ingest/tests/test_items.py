"""Items (ingest/items): a box placed in space from a depth map, observations of one thing joined
across frames, the turning of sideways frames, and the stored index searched by words. The models
are stood in for: what is tested is the geometry and the joining, on a made-up room whose things'
places are known."""

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from ingest.items import frames as sources
from ingest.items import models, run, search, store
from ingest.items.lift import Frame, place
from ingest.items.merge import Observation, merge
from ingest.vault import Vault

W, H, F = 640, 480, 500.0
K = (F, F, W / 2, H / 2)


def camera(x: float, yaw_deg: float = 0.0) -> np.ndarray:
    """Camera to world (OpenGL axes) at (x, 1.5, 0), turned about the vertical."""
    a = np.radians(yaw_deg)
    c2w = np.eye(4)
    c2w[:3, :3] = [[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]]
    c2w[:3, 3] = [x, 1.5, 0.0]
    return c2w


THINGS = {  # name: (centre in the world, size in metres, colour seed)
    "chair": (np.array([0.0, 1.0, -3.0]), 0.6, 1),
    "lamp": (np.array([1.5, 1.6, -4.0]), 0.4, 2),
    "box": (np.array([-1.5, 0.8, -2.5]), 0.5, 3),
}


def vector(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=32)
    return (v / np.linalg.norm(v)).astype(np.float32)


def render(c2w: np.ndarray, index: int, t: float, wall_m: float = 6.0) -> tuple[Frame, dict]:
    """A frame of the room from this camera: a wall ``wall_m`` ahead and each thing as a flat
    square at its depth; returns the frame and each visible thing's box."""
    depth = np.full((H // 4, W // 4), wall_m, np.float32)
    w2c = np.linalg.inv(c2w)
    boxes = {}
    for name, (centre, size, _) in THINGS.items():
        cam = w2c[:3, :3] @ centre + w2c[:3, 3]
        z = -cam[2]
        if z <= 0.3:
            continue
        u, v = cam[0] / z * F + W / 2, -cam[1] / z * F + H / 2
        half = size / 2 / z * F
        x0, y0, x1, y1 = u - half, v - half, u + half, v + half
        if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
            continue
        depth[int(y0 / 4) : int(np.ceil(y1 / 4)), int(x0 / 4) : int(np.ceil(x1 / 4))] = z
        boxes[name] = (x0, y0, x1, y1)
    return Frame(index, t, np.zeros((H, W, 3), np.uint8), K, depth, None, "lidar", c2w), boxes


def test_a_box_is_placed_where_its_depth_says() -> None:
    frame, boxes = render(camera(0.0), 0, 0.0)
    where = place(frame, boxes["chair"])
    assert where is not None and where.depth == "lidar"
    assert np.allclose(where.position, THINGS["chair"][0], atol=0.06)
    assert where.size_m[0] == pytest.approx(0.6, abs=0.03) and where.distance_m == pytest.approx(3.0, abs=0.01)
    # A box wider than the thing still lands on the thing, not on the wall behind its edges.
    x0, y0, x1, y1 = boxes["chair"]
    loose = place(frame, (x0 - 25, y0 - 25, x1 + 25, y1 + 25))
    assert loose is not None and loose.distance_m == pytest.approx(3.0, abs=0.01)


def test_depth_beyond_the_lidar_is_marked_an_estimate_and_no_depth_places_nothing() -> None:
    frame, _ = render(camera(0.0), 0, 0.0, wall_m=9.0)
    far = place(frame, (20, 20, 80, 80))  # only the wall, 9 m away
    assert far is not None and far.depth == "estimate" and far.distance_m == pytest.approx(9.0, abs=0.01)
    bare = Frame(0, 0.0, frame.image, K, None)
    assert place(bare, (20, 20, 80, 80)) is None
    untrusted = Frame(0, 0.0, frame.image, K, frame.depth, np.zeros_like(frame.depth, bool), "lidar", frame.c2w)
    assert place(untrusted, (20, 20, 80, 80)) is None
    model = Frame(0, 0.0, frame.image, K, frame.depth, None, "model")
    there = place(model, (20, 20, 80, 80))
    assert there is not None and there.depth == "model" and there.position[2] == pytest.approx(-9.0, abs=0.01)


def observations(noise_m: float = 0.03) -> tuple[list[Observation], dict[int, Frame]]:
    rng = np.random.default_rng(0)
    out, frames = [], {}
    for i, (x, yaw) in enumerate([(-1.0, -12), (-0.5, -6), (0.0, 0), (0.5, 6), (1.0, 12), (0.2, 3)]):
        frame, boxes = render(camera(x, yaw), i, float(i))
        frames[i] = frame
        for name, box in boxes.items():
            where = place(frame, box)
            assert where is not None
            v = vector(THINGS[name][2]) + rng.normal(0, 0.05, 32).astype(np.float32)
            out.append(Observation(i, float(i), box, name, 1.0, where.position + rng.normal(0, noise_m, 3), where.size_m,
                                   where.distance_m, where.depth, v / np.linalg.norm(v)))  # fmt: skip
    return out, frames


def test_the_same_thing_seen_from_several_frames_is_one_item_at_its_place() -> None:
    obs, frames = observations()
    items = merge(obs)
    assert sorted(i.label for i in items) == ["box", "chair", "lamp"]
    looks = {name: sum(o.label == name for o in obs) for name in THINGS}
    assert looks["chair"] == 6 and min(looks.values()) >= 2
    for item in items:
        assert len(item.observations) == looks[item.label]  # every look at it, and no other thing's
        assert np.linalg.norm(item.position - THINGS[item.label][0]) < 0.1
        assert item.spread_m < 0.08
    check = run.reprojection(items, frames)
    assert check["inside_box"] == 1.0 and check["median_offset_box_widths"] < 0.2


def test_alike_things_apart_and_unlike_things_together_stay_separate() -> None:
    def seen(position: list[float], seed: int, t: float, size: float = 0.5, label: str = "chair") -> Observation:
        return Observation(
            int(t), t, (0, 0, 10, 10), label, 1.0, np.array(position, float), (size, size), 2.0, "lidar", vector(seed)
        )

    twins = merge([seen([0, 0, -2], 1, 0.0), seen([3, 0, -2], 1, 1.0), seen([0.05, 0, -2], 1, 2.0)])
    assert sorted(len(i.observations) for i in twins) == [1, 2]  # two chairs alike, 3 m apart
    cup_on_table = merge([seen([0, 0.8, -2], 5, 0.0, 1.6, "table"), seen([0.1, 0.9, -2], 6, 1.0, 0.1, "cup")])
    assert len(cup_on_table) == 2
    # Seen at the start and again at the end of a walk, after other things: still one.
    again = merge(
        [seen([0, 0, -2], 1, 0.0), seen([5, 0, -2], 2, 1.0), seen([9, 0, -2], 3, 2.0), seen([0.1, 0, -2.1], 1, 9.0)]
    )
    assert sorted(len(i.observations) for i in again) == [1, 1, 2]


@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_a_box_found_in_the_upright_picture_goes_back_to_the_stored_frame(turn: int) -> None:
    stored = np.zeros((48, 64, 3), np.uint8)
    stored[10:20, 30:50] = 255  # a bright patch: x 30-50, y 10-20
    up = sources.upright(stored, turn)
    ys, xs = np.nonzero(up[:, :, 0])
    box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    assert sources.box_back(box, turn, 64, 48) == (30, 10, 50, 20)


def test_items_are_stored_and_found_by_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    obs, frames = observations()
    items = merge(obs)
    crops = [np.full((40, 30, 3), 100, np.uint8) for _ in items]
    record = store.write(tmp_path / "it_0123456789ab", items, crops, source={"kind": "scan", "file": "x"}, frame="world",
                         models={"detector": "stand-in"}, frames=[{"index": 0, "t": 0.0, "caption": "a room"}])  # fmt: skip
    assert record["id"] == "it_0123456789ab" and [i["id"] for i in record["items"]] == ["i0001", "i0002", "i0003"]
    back, vectors = store.read(tmp_path / "it_0123456789ab")
    assert back == json.loads((tmp_path / "it_0123456789ab" / "items.json").read_text(encoding="utf-8"))
    assert vectors.shape == (3, 32) and np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-2)
    assert all((tmp_path / "it_0123456789ab" / i["crop"]).stat().st_size > 100 for i in back["items"])
    lamp = next(i for i in back["items"] if i["label"] == "lamp")
    assert np.allclose(lamp["position"], THINGS["lamp"][0], atol=0.1) and lamp["seen"] >= 2 and lamp["depth"] == "lidar"

    # Words whose embedding is the lamp's picture's find the lamp; the name alone lifts an exact word.
    by_seed = {"a photo of the light": 2, "a photo of chair": 99}
    embed = lambda texts: np.stack([vector(by_seed[t]) for t in texts])  # noqa: E731
    hits = search.search("the light", [tmp_path / "it_0123456789ab"], embed)
    assert hits[0]["label"] == "lamp" and hits[0]["score"] > 0.9
    hits = search.search("chair", [tmp_path / "it_0123456789ab"], embed)
    assert hits[0]["label"] == "chair"  # nothing looks like seed 99: the name decides


def test_one_frame_is_looked_at_turned_upright_and_its_things_placed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A phone held upright films sideways: the detector sees the picture turned, and its boxes come
    back to the stored frame before they are placed."""
    frame, boxes = render(camera(0.0), 3, 1.5)
    sideways = Frame(frame.index, frame.t, frame.image, frame.K, frame.depth, None, "lidar", frame.c2w, 90)
    seen_shapes = []

    def look(picture: np.ndarray, caption: bool = True) -> models.Look:
        seen_shapes.append(picture.shape[:2])
        x0, y0, x1, y1 = boxes["chair"]  # in the upright picture: (x', y') = (H - y, x)
        return models.Look([models.Detection((H - y1, x0, H - y0, x1), "chair")], "a chair")

    monkeypatch.setattr(models, "look", look)
    found, entry = run.observe(sideways)
    assert seen_shapes == [(W, H)]
    assert len(found) == 1 and np.allclose(found[0].position, THINGS["chair"][0], atol=0.06)
    assert entry == {"index": 3, "t": 1.5, "caption": "a chair", "text": ""}


def test_the_api_lists_serves_and_finds_items_by_name(client: TestClient, vault: Vault) -> None:
    obs, _ = observations()
    items = merge(obs)
    folder = store.folder(vault, "2026-10-24", "it_0123456789ab")
    store.write(folder, items, [np.full((40, 30, 3), 100, np.uint8) for _ in items], source={"kind": "scan", "file": "trail"},
                frame="world", models={"detector": "stand-in"}, frames=[{"index": 0, "t": 0.0, "caption": "a room"}])  # fmt: skip
    listed = client.get("/items").json()
    assert [(s["date"], s["id"], s["items"], s["frame"]) for s in listed] == [
        ("2026-10-24", "it_0123456789ab", 3, "world")
    ]
    record = client.get("/items/2026-10-24/it_0123456789ab").json()
    assert sorted(i["label"] for i in record["items"]) == ["box", "chair", "lamp"]
    crop = client.get(f"/items/2026-10-24/it_0123456789ab/{record['items'][0]['crop']}")
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/jpeg" and crop.content[:2] == b"\xff\xd8"
    found = client.get("/items/find", params={"q": "lamp"}).json()
    assert len(found) == 1 and found[0]["label"] == "lamp" and found[0]["date"] == "2026-10-24"
    assert np.allclose(found[0]["position"], THINGS["lamp"][0], atol=0.1)
    assert client.get("/items/find", params={"q": "sofa"}).json() == []
    # Nothing outside an items set is reachable through these routes.
    for bad in ("/items/2026-10-24/it_zzz", "/items/2026-10-24/it_0123456789ab/crops/..%2Fitems.json",
                "/items/2026-10-24/it_0123456789ab/crops/i9999.jpg", "/items/2026-13-40/it_0123456789ab"):  # fmt: skip
        assert client.get(bad).status_code == 404

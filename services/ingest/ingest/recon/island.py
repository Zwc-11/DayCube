"""A shot of a video as an island of the world (docs/world.md, "Islands").

Version 1 is one view: the sharpest frame of the shot with the fewest people in it, lifted to 3D
by a metric depth model and stood on the map where it was filmed. It holds what one view holds:
the ground, and the walls beside the way, within about 35 m. A walk through many frames needs a
model that solves the frames' poses and depth together (MapAnything, VGGT) on the GPU; plain
structure from motion confused turning with walking on the first test (a forward walk along a
facade at dusk, docs/world.md), and depth chained frame to frame under-read the walk five times.

Steps, each its own function:

1. ``keyframe``: the sharpest frame (variance of the Laplacian) with the least of it masked.
2. ``moving``, ``hole``: people, vehicles and bicycles out (DeepLabV3 MobileNet, torchvision, BSD),
   widened, and carried down over the feet and shadows the segmenter misses; nobody passing by is
   kept in the vault's 3D. ``fill`` paints what was behind them (LaMa) and ``depth_behind`` gives
   it depth; that island is labelled imagined.
3. Depth: Depth Anything V2 Metric Outdoor (Apache-2.0). Its metres were 2-3x long on the dusk
   test, so two rulers in the frame set the scale:
   - ``people_ruler``: people standing whole in the frame are taken as 1.70 m tall;
   - ``ground_ruler``: the camera is taken as 1.45 m above the ground under it (a phone held to
     film while walking), the ground found by RANSAC.
   Their median is used; both are kept in world.json's notes.
4. ``upright``: the ground's normal is up; the view's forward along the ground is -z.
5. ``facade``: the largest vertical plane beside the camera, the wall the island is placed by.
6. ``crop``: within 35 m, no sky, nothing high on the far side of the street (towers hundreds of
   metres away come out of one view flattened onto a sheet; the map layer draws them).
7. ``place_on_wall``: the facade laid on a wall of the building's open footprint (OSM), the camera
   ``along`` metres from the wall's first corner where the shot starts, plus a walking speed times
   the keyframe's time into the shot; on the side the wall faces. The wall is named, or found from
   a landmark (``where``, ``choose``; world/walls.py).

Run in services/recon's environment (PyTorch, transformers, OpenCV 5):

    uv run --project services/recon python -m ingest.recon.island VIDEO --date D --shot T0 T1 \
        (--at LANDMARK [--street NAME] [--pick N] | --wall LAT,LON LAT,LON --along M) [--title T] [--vault PATH]
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar

import numpy as np

from ..world.walls import Seen, Wall

T = TypeVar("T")
ADULT_M = 1.70
CAMERA_M = 1.45
WALK = 1.3  # m/s: the step cadence on the first test was about 2 a second, an ordinary walk
KEEP_M = 35.0
MOVING = (2, 6, 7, 14, 15, 19)  # VOC: bicycle, bus, car, motorbike, person, train
PERSON = 15
DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf"
# LaMa (Apache-2.0) as OpenCV's model zoo exports it, 93 MB: OpenCV 5's DNN runs it, no new runtime.
LAMA = ("opencv/inpainting_lama", "inpainting_lama_2025jan.onnx", "aee6d22f0a13e5e35af1c9a1c3afd62841fc6f3f")


@dataclass(frozen=True)
class Plane:
    normal: np.ndarray  # unit
    point: np.ndarray

    def distance(self, p: np.ndarray) -> np.ndarray:
        return (p - self.point) @ self.normal


def ransac_plane(points: np.ndarray, accept: Callable[[np.ndarray], bool], tolerance: float, rounds: int = 600, seed: int = 0) -> tuple[Plane, int]:
    """The plane through the most ``points`` within ``tolerance``, among those whose normal
    ``accept``s (a function of the unit normal)."""
    rng = np.random.default_rng(seed)
    best: tuple[Plane | None, int] = (None, 0)
    for _ in range(rounds):
        a, b, c = points[rng.choice(len(points), 3, replace=False)]
        n = np.cross(b - a, c - a)
        length = np.linalg.norm(n)
        if length < 1e-9 or not accept(n / length):
            continue
        n = n / length
        count = int((np.abs((points - a) @ n) < tolerance).sum())
        if count > best[1]:
            best = (Plane(n, a), count)
    if best[0] is None:
        raise ValueError("No plane found")
    return best[0], best[1]


def ground_ruler(cam: np.ndarray) -> tuple[float, np.ndarray]:
    """The scale making the camera ``CAMERA_M`` above the ground, and the ground's normal pointing
    down (camera axes: x right, y down, z ahead). ``cam``: points in the camera's frame."""
    near = cam[(cam[:, 1] > 0.3) & (cam[:, 2] < 12) & (np.abs(cam[:, 0]) < 3)]
    plane, _ = ransac_plane(near[:: max(1, len(near) // 30_000)], lambda n: abs(n[1]) > 0.85, 0.04)
    down = plane.normal if plane.normal[1] > 0 else -plane.normal
    return CAMERA_M / float(plane.point @ down), down


def people_ruler(heights_px: list[float], depths: list[float], f: float) -> float | None:
    """The scale making the people standing whole in the frame ``ADULT_M`` tall (median)."""
    tall = [h * d / f for h, d in zip(heights_px, depths, strict=True)]
    return ADULT_M / float(np.median(tall)) if tall else None


def upright(down: np.ndarray) -> np.ndarray:
    """Rows of the rotation from the camera's axes to the island's: x right, y up, z back (the
    view's forward along the ground is -z)."""
    up = -down / np.linalg.norm(down)
    ahead = np.array([0.0, 0.0, 1.0]) - up * up[2]
    ahead /= np.linalg.norm(ahead)
    return np.stack([np.cross(ahead, up), up, -ahead])


def facade(points: np.ndarray, side: float = 1.0) -> tuple[np.ndarray, float]:
    """The largest vertical plane on one side of the camera (``side`` 1: right, -1: left), 3 to 15 m
    up: its normal (horizontal, towards the camera) and the camera's distance from it. Island
    axes, the camera's foot at the origin."""
    wall = points[(points[:, 0] * side > 1.5) & (points[:, 1] > 3) & (points[:, 1] < 15)]
    plane, _ = ransac_plane(wall[:: max(1, len(wall) // 30_000)], lambda n: abs(n[1]) < 0.1, 0.15, rounds=800)
    n = plane.normal * np.array([1.0, 0.0, 1.0])
    n /= np.linalg.norm(n)
    if n @ -plane.point < 0:
        n = -n
    return n, float(n @ -plane.point)


def facade_span(points: np.ndarray, normal: np.ndarray, distance: float) -> tuple[float, float]:
    """Where the facade was seen, from and to how many metres ahead of the camera along it."""
    along = np.cross(normal, [0.0, 1.0, 0.0])
    if along[2] > 0:
        along = -along  # ahead is -z
    on =points[(np.abs(points @ normal + distance) < 0.3) & (points[:, 1] > 0.5)] @ along
    return float(np.percentile(on, 2)), float(np.percentile(on, 98))


def crop(points: np.ndarray, rgb: np.ndarray, camera_height: float = CAMERA_M) -> np.ndarray:
    """What one view holds up (island axes): within ``KEEP_M`` of the camera, no sky (blue, high),
    nothing above 15 m, and on the far side of the camera only the street (below 4 m)."""
    far = np.linalg.norm(points - np.array([0.0, camera_height, 0.0]), axis=1)
    c = rgb.astype(int)
    sky = (c[:, 2] > c[:, 0] + 20) & (c[:, 2] > 110) & (points[:, 1] > 5)
    across = (points[:, 0] < -1) & (points[:, 1] > 4)
    return (far < KEEP_M) & ~sky & (points[:, 1] < 15) & ~across & (points[:, 0] > -6)


def place_on_wall(a: tuple[float, float], b: tuple[float, float], along: float, normal: np.ndarray,
                  distance: float, side: float = 1.0) -> tuple[float, float, np.ndarray]:
    """Where the camera's foot is (lat, lon) and the rotation (3 x 3) from the island's axes to the
    world's (x east, y up, z south), with the island's facade ``normal`` laid on the wall from
    ``a`` to ``b`` (lat, lon, in the walking direction): the camera ``along`` metres from ``a``
    and ``distance`` out from the wall, on its left when the wall was on its right (``side`` 1)."""
    from ..world.geo import lat_lon_from_local, local_from_lat_lon

    bx, bz = local_from_lat_lon(*a, *b)
    wall = np.array([bx, 0.0, bz]) / math.hypot(bx, bz)
    out = side * np.array([wall[2], 0.0, -wall[0]])  # the wall turned a quarter to the walk's left
    yaw = math.atan2(out[0], -out[2]) - math.atan2(normal[0], -normal[2])
    c, s = math.cos(yaw), math.sin(yaw)
    rotation = np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]])
    foot = wall * along + out * distance
    lat, lon = lat_lon_from_local(*a, float(foot[0]), float(foot[2]))
    return lat, lon, rotation


def splat_records(points: np.ndarray, rgb: np.ndarray, size: np.ndarray) -> bytes:
    """The viewer's 32-byte splat records: position, scale (3 x f32), RGBA, rotation (identity)."""
    rec = np.zeros(len(points), dtype=[("p", "<f4", 3), ("s", "<f4", 3), ("c", "u1", 4), ("q", "u1", 4)])
    rec["p"] = points
    rec["s"] = np.repeat(size[:, None], 3, 1)
    rec["c"][:, :3] = rgb
    rec["c"][:, 3] = 255
    rec["q"] = (255, 128, 128, 128)
    return rec.tobytes()


def keyframe(video: Path, t0: float, t1: float, fps: float = 8.0) -> tuple[np.ndarray, float]:
    """The shot's sharpest frame with the least masked (people, vehicles), and its time."""
    import cv2

    capture = cv2.VideoCapture(str(video))
    best: tuple[float, np.ndarray | None, float] = (-1.0, None, t0)
    t = t0 + 0.4  # past the cut
    while t < t1 - 0.2:
        capture.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, bgr = capture.read()
        if ok:
            rgb = bgr[:, :, ::-1].copy()
            score = cv2.Laplacian(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var() * (1 - 2.5 * moving(rgb).mean())
            if score > best[0]:
                best = (score, rgb, t)
        t += 1 / fps
    if best[1] is None:
        raise ValueError("No frame could be read from the shot")
    return best[1], best[2]


def segment(rgb: np.ndarray) -> np.ndarray:
    """VOC labels per pixel (DeepLabV3 MobileNet)."""
    import cv2
    import torch
    import torchvision
    from PIL import Image

    if not hasattr(segment, "model"):
        weights = torchvision.models.segmentation.DeepLabV3_MobileNet_V3_Large_Weights.DEFAULT
        segment.model = torchvision.models.segmentation.deeplabv3_mobilenet_v3_large(weights=weights).eval()
        segment.prepare = weights.transforms()
    with torch.inference_mode():
        labels = segment.model(segment.prepare(Image.fromarray(rgb)).unsqueeze(0))["out"][0].argmax(0)
    return cv2.resize(labels.numpy().astype(np.uint8), (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_NEAREST)


def moving(rgb: np.ndarray, labels: np.ndarray | None = None) -> np.ndarray:
    import cv2

    labels = segment(rgb) if labels is None else labels
    return cv2.dilate(np.isin(labels, MOVING).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0


def hole(gone: np.ndarray) -> np.ndarray:
    """What is taken out of the frame and filled: ``gone`` widened by a 35th of the frame's width,
    then carried down a 25th of its height (the segmenter misses feet, shoes and shadows, and LaMa
    grows whatever is left of a person back into the hole)."""
    import cv2

    h, w = gone.shape
    k, down = w // 35 | 1, h // 25
    wide = cv2.dilate(gone.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    return cv2.dilate(wide, np.ones((down, 1), np.uint8), anchor=(0, down - 1)) > 0


def fill(rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """``rgb`` with ``hole`` painted in by LaMa (at 512 x 512, its export's fixed size)."""
    import cv2
    from huggingface_hub import hf_hub_download

    if not hasattr(fill, "net"):
        fill.net = cv2.dnn.readNetFromONNX(hf_hub_download(LAMA[0], LAMA[1], revision=LAMA[2]))
    h, w = hole.shape
    fill.net.setInput(cv2.dnn.blobFromImage(rgb, 1 / 255, (512, 512)), "image")
    fill.net.setInput((cv2.dnn.blobFromImage(hole.astype(np.uint8), 1.0, (512, 512)) > 0).astype(np.float32), "mask")
    painted = np.clip(fill.net.forward()[0].transpose(1, 2, 0), 0, 255).astype(np.uint8)
    return np.where(hole[..., None], cv2.resize(painted, (w, h), interpolation=cv2.INTER_CUBIC), rgb)


def depth_behind(d: np.ndarray, d_filled: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """``d`` with depth in ``hole``: the depth model's reading of the filled frame, times its ratio
    to ``d`` carried in from the hole's edge, so the two meet there. (The model reads the whole
    frame a little differently once people are gone: 10% median on the dusk test.)"""
    import cv2

    ratio = np.where(hole, 1.0, d / np.maximum(d_filled, 1e-3)).astype(np.float32)
    # Navier-Stokes: OpenCV 5's Telea returns about half the value on float images.
    ratio = cv2.inpaint(ratio, hole.astype(np.uint8), 3, cv2.INPAINT_NS)
    return np.where(hole, d_filled * ratio, d)


def depth(rgb: np.ndarray) -> np.ndarray:
    import torch
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    if not hasattr(depth, "model"):
        depth.processor = AutoImageProcessor.from_pretrained(DEPTH_MODEL)
        depth.model = AutoModelForDepthEstimation.from_pretrained(DEPTH_MODEL).eval()
    with torch.inference_mode():
        out = depth.model(**depth.processor(images=rgb, return_tensors="pt")).predicted_depth
    h, w = rgb.shape[:2]
    return torch.nn.functional.interpolate(out[None], size=(h, w), mode="bilinear", align_corners=False)[0, 0].numpy()


def build(rgb: np.ndarray, f: float) -> tuple[list[tuple[bytes, int, np.ndarray]], dict]:
    """The islands (splat records, count, points; island axes, the camera's foot at the origin)
    from one frame: what it saw, then what was filled in where people and vehicles were; and what
    was measured on the way."""
    import cv2

    h, w = rgb.shape[:2]
    labels = segment(rgb)
    gone = hole(moving(rgb, labels))
    d = depth(rgb)
    count, parts, stats, _ = cv2.connectedComponentsWithStats((labels == PERSON).astype(np.uint8))
    whole = [k for k in range(1, count) if stats[k][4] > 4000 and stats[k][1] > 2 and stats[k][1] + stats[k][3] < h - 2
             and stats[k][3] / max(stats[k][2], 1) > 1.8]  # standing and not cut by the frame
    by_people = people_ruler([float(stats[k][3]) for k in whole], [float(np.median(d[parts == k])) for k in whole], f)
    v, u = np.mgrid[0:h:2, 0:w:2]

    def lift(depth_map: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Every 2nd pixel under ``mask`` in the camera's frame (x right, y down, z ahead)."""
        uu, vv, zz = u[mask], v[mask], depth_map[v, u][mask]
        return np.stack([(uu - w / 2) / f * zz, (vv - h / 2) / f * zz, zz], 1)

    seen = ~gone[v, u] & (d[v, u] < 70)
    by_ground, down = ground_ruler(lift(d, seen))
    scale = float(np.median([s for s in (by_people, by_ground) if s]))
    turn = upright(down)

    def to_island(cam: np.ndarray) -> np.ndarray:
        return (cam * scale) @ turn.T + np.array([0.0, CAMERA_M, 0.0])

    points, colour = to_island(lift(d, seen)), rgb[v, u][seen]
    normal, distance = facade(points)
    # Where people and vehicles were: invented, so kept apart as an imagined island the viewer
    # labels and can hide.
    rgb_fill = fill(rgb, gone)
    d_fill = depth_behind(d, depth(rgb_fill), gone)
    filled = gone[v, u] & (d_fill[v, u] < 70)
    guessed, guessed_colour = to_island(lift(d_fill, filled)), rgb_fill[v, u][filled]
    islands = []
    for pts, col in ((points, colour), (guessed, guessed_colour)):
        keep = crop(pts, col)
        pts, col = pts[keep], col[keep]
        size = np.maximum(0.012, 2 * np.linalg.norm(pts - np.array([0.0, CAMERA_M, 0.0]), axis=1) / f)
        islands.append((splat_records(pts, col, size), len(pts), pts))
    span = facade_span(islands[0][2], normal, distance)
    notes = {"scale": round(scale, 3), "scale_by_people": by_people and round(by_people, 3),
             "scale_by_ground": round(by_ground, 3), "people": len(whole), "facade_m": round(distance, 2),
             "facade_seen_m": [round(s, 1) for s in span], "points": islands[0][1], "imagined_points": islands[1][1]}
    return islands, {"normal": normal, "distance": distance, "span": span, "notes": notes}


def retried(call: Callable[[], T], tries: int = 4) -> T:
    """``call``, tried again after a dropped connection (Overpass and Open-Meteo reset some)."""
    import time

    import httpx

    for attempt in range(tries - 1):
        try:
            return call()
        except httpx.TransportError:
            time.sleep(3 * (attempt + 1))
    return call()


def where(args: argparse.Namespace, maps: Path) -> tuple[Wall | str, Callable[[Seen], Wall | None], float]:
    """Before the models run, so that a network failure or a question costs nothing: the wall the
    shot was beside (or the walls to pick from), the same wall as it fits what the island saw,
    and the ground's height at its start."""
    from ..integrations.nominatim import NominatimAdapter
    from ..integrations.overpass import OverpassAdapter
    from ..world import geo, osm
    from ..world.walls import ANY, landmark_walls, reach

    if args.wall:
        a, b = (tuple(float(x) for x in corner.split(",")) for corner in args.wall)
        wall = Wall(a, b, 0.0, None, (-math.inf, math.inf))
        return wall, lambda seen: wall, retried(lambda: geo.ground_height(*a, maps))
    place = retried(lambda: NominatimAdapter(maps / "places").place(args.at))
    overpass, radius = OverpassAdapter(maps / "osm"), reach(place)
    buildings = osm.footprints(retried(lambda: overpass.buildings(place["lat"], place["lon"], radius)))
    streets = retried(lambda: overpass.streets(place["lat"], place["lon"], radius))
    wall = choose(landmark_walls(place, buildings, streets, ANY), args.street, args.pick)
    if isinstance(wall, str):
        return wall, lambda seen: None, math.nan
    at = wall.a if math.hypot(*geo.local_from_lat_lon(place["lat"], place["lon"], *wall.a)) > 50 else (place["lat"], place["lon"])

    def fitted(seen: Seen) -> Wall | None:
        return next((w for w in landmark_walls(place, buildings, streets, seen) if (w.a, w.b) == (wall.a, wall.b)), None)

    return wall, fitted, retried(lambda: geo.ground_height(*at, maps))


def choose(walls: list[Wall], street: str | None, pick: int | None) -> Wall | str:
    """The wall the shot was beside, or the walls to pick from: those facing a street (the one
    named, if any), longest first. Numbered before any shot is measured, so a pick stays put."""
    from ..world.walls import label

    middle = tuple(np.mean([w.a for w in walls] + [w.b for w in walls], 0)) if walls else (0.0, 0.0)
    walls = [w for w in walls if w.street] or walls
    if street:
        walls = [w for w in walls if w.street and street.casefold() in w.street.casefold()]
    if pick is not None and 1 <= pick <= len(walls):
        return walls[pick - 1]
    if len(walls) == 1 and pick is None:
        return walls[0]
    rows = [f"  {n}  {w.street or '(no street)'}, {w.length:.0f} m, {label(w, middle)}: {w.a[0]:.6f},{w.a[1]:.6f} to {w.b[0]:.6f},{w.b[1]:.6f}"
            for n, w in enumerate(walls, 1)]  # fmt: skip
    head = f"{len(walls)} walls fit; pick one with --pick N (or name one with --wall)" if walls else "No wall fits"
    return "\n".join([head + (f" on {street!r}" if street else "") + ":", *rows])


def main() -> None:
    from ..config import Settings
    from ..vault import Vault
    from ..world import store

    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("video", type=Path)
    p.add_argument("--date", required=True)
    p.add_argument("--shot", nargs=2, type=float, required=True, metavar=("T0", "T1"))
    on = p.add_mutually_exclusive_group(required=True)
    on.add_argument("--wall", nargs=2, metavar=("LAT,LON", "LAT,LON"), help="the footprint wall beside the walk, from where the walk starts")
    on.add_argument("--at", metavar="LANDMARK", help="the building the shot was filmed beside, e.g. \"Union Station, Toronto\"")
    p.add_argument("--street", help="with --at: the street the walk was on")
    p.add_argument("--pick", type=int, help="with --at: which of the walls it lists")
    p.add_argument("--along", type=float, help="metres from the wall's first corner where the shot starts (with --at: the middle of where it fits)")
    p.add_argument("--speed", type=float, default=WALK, help="walking speed, m/s: the keyframe is this far on per second")
    p.add_argument("--focal", type=float, default=1.45, help="focal length over the frame's width (iPhone main camera, 3:4 crop)")
    p.add_argument("--title", default="")
    p.add_argument("--error-m", type=float, default=15.0)
    p.add_argument("--vault", type=Path)
    args = p.parse_args()
    if args.wall and args.along is None:
        p.error("--wall needs --along")
    if args.wall and (args.street or args.pick is not None):
        p.error("--street and --pick choose among the walls --at finds")
    vault = Vault(args.vault or Settings().vault_root)
    wall, fitted, ground = where(args, vault.root / "maps")
    if isinstance(wall, str):
        p.exit(2, wall + "\n")
    rgb, t = keyframe(args.video, *args.shot)
    islands, found = build(rgb, args.focal * rgb.shape[1])
    seen_by = Seen(found["distance"], *found["span"])
    if (wall := fitted(seen_by)) is None:
        p.exit(2, f"The facade seen ({seen_by.start:.0f} to {seen_by.end:.0f} m ahead) does not fit that wall, or its sidewalk is built on\n")
    if args.along is not None:
        along, error = args.along + args.speed * (t - args.shot[0]), args.error_m
    else:  # anywhere it fits: the middle, the error widened to cover the rest
        along, error = sum(wall.along) / 2, max(args.error_m, (wall.along[1] - wall.along[0]) / 2 + 3)
    lat, lon, rotation = place_on_wall(wall.a, wall.b, along, found["normal"], found["distance"])
    enu = np.eye(4)
    enu[:3, :3] = rotation

    def island(n: int, pts: np.ndarray, count: int, kind: str) -> dict:
        placed = pts @ rotation.T
        return {"id": f"is_{n:02d}", "splat": f"is_{n:02d}.splat", "points": count, "enu": enu.round(6).tolist(),
                "bounds": {"min": placed.min(0).round(2).tolist(), "max": placed.max(0).round(2).tolist()},
                "located_by": "landmark+footprints", "error_m": round(error, 1), "kind": kind, "shots": [list(args.shot)]}

    seen = island(1, islands[0][2], islands[0][1], "seen")
    seen["view"] = {"eye": (rotation @ [0.0, CAMERA_M, 0.0]).round(3).tolist(),
                    "look": (rotation @ [0.0, CAMERA_M, -10.0]).round(3).tolist()}
    world = {
        "id": store.new_id(), "date": args.date, "title": args.title or args.video.stem,
        "made": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"file": args.video.name, "t0_s": args.shot[0], "t1_s": args.shot[1]},
        "origin": {"lat": lat, "lon": lon, "ground_m": round(ground, 2)},
        "islands": [seen] + ([island(2, islands[1][2], islands[1][1], "imagined")] if islands[1][1] else []),
    }
    path = store.save(vault, args.date, world, {i["splat"]: data for i, (data, _, _) in zip(world["islands"], islands, strict=False)})
    print(path, {"keyframe_s": round(t, 2), "wall": [wall.a, wall.b], "street": wall.street, "along_m": round(along, 1), **found["notes"]})


if __name__ == "__main__":
    main()

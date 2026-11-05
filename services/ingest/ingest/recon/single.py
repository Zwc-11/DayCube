"""One photo: how much of the 3D world it holds alone, and how exactly it lands in a scanned one
(docs/glasses.md, "One photo").

Glasses take a photo now and then, with no position. A single photo can be used two ways, and both
are measured on a phone scan, whose ARKit poses say where every frame really was:

- **Alone (lift).** The photo becomes 3D by itself:
  - a depth for every pixel makes one splat per pixel, sized to the pixel's footprint. The depth
    comes from the phone's LiDAR (what glasses do not have, as a reference), or from a model
    that reads it off the image: Depth Pro, Depth Anything V2 metric;
  - or SHARP predicts 3D Gaussians from the image directly.

  It is rendered where the phone really was 0.25, 0.5, 1, 2 and 4 m further along the walk, and
  compared with the frame the phone took there. Scored on the pixels the photo covers, against
  the flat photo on the same pixels (no 3D at all), with the share it covers. The models' depth
  is also compared with the LiDAR's, where the LiDAR is reliable.
- **Into the scan (locate).** The place was scanned: here the same scan's frames every 0.5 m with
  ARKit's poses and LiDAR depth, leaving out those within 1 s of the photo.
  - The photo is matched (ALIKED + LightGlue) with the scan frames nearest its GPS fix.
  - The scan's depth makes the matches 3D.
  - PnP gives where the photo was taken, scored against ARKit (metres, degrees).
  - The scan is then rendered from there and compared with the photo: what the scan predicts
    the photo shows.

The renderer is a z-buffered splat per point (``render``), in PyTorch on the GPU when there is
one. SHARP's Gaussians are drawn as opaque discs of their size, an approximation of their own
blending (``gsplat`` would need building on Windows).

Everything stays under ``work`` (the photos are the person's own).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..phone.geo import local
from ..skeleton.parsers.types import Fix
from ..splat import dataset
from . import align, simulate

AHEAD_M = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)  # where the photo is rendered, along the walk from it (0: its own pose)
PHOTOS = 8
WIDTH = 640  # rendered and scored at this width (480 tall for a 4:3 video)
MARGIN_M = 6.0  # photos this far from either end of the walk, so 4 m ahead exists
MAP_STEP_M = 0.5  # the scan: a frame every this far along the walk
EXCLUDE_S = 1.0  # scan frames this close in time to the photo are left out
NEAREST = 12  # scan frames matched with each photo
PRIOR_RADIUS_M = 20.0  # around the GPS fix
GPS_SIGMA_M = 2.0  # a simulated GPS fix when there is no GPS (the phone's own fit: 2.0 m)
LIDAR_RANGE_M = 5.5  # LiDAR depth beyond this is ARKit's estimate
MAX_DEPTH_M = 30.0
MAX_RADIUS_PX = 4
OFFSETS = (-3, -2, -1, 0, 1, 2, 3)  # frames: the pose-timing check
SHARP_OPACITY = 0.3
SHARP_URL = "https://ml-site.cdn-apple.com/models/sharp/sharp_2572gikvuh.pt"

METHODS = {
    "flat": "the photo itself, no 3D",
    "lidar": "the phone's LiDAR depth (glasses have none): the reference",
    "depth-pro": "Depth Pro (Apple, research licence): metric depth from the photo, given its focal length",
    "dav2-metric": "Depth Anything V2 Metric Outdoor Large (Apache-2.0 per its model card): metric depth from the photo",
    "sharp": "SHARP (Apple, research licence): 3D Gaussians from the photo",
}
DEPTH_MODELS = {
    "depth-pro": "apple/DepthPro-hf",
    "dav2-metric": "depth-anything/Depth-Anything-V2-Metric-Outdoor-Large-hf",
}

# A depth model: (RGB image, (fx, fy, cx, cy) at its size) -> depth in metres at its size.
DepthModel = Callable[[np.ndarray, np.ndarray], np.ndarray]


def device() -> object:
    import torch

    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


@dataclass
class Cloud:
    """Points to draw: world positions, colours (uint8) and each one's size in metres."""

    xyz: np.ndarray
    rgb: np.ndarray
    size_m: np.ndarray


def scale_K(K: np.ndarray, s: float) -> np.ndarray:
    """Intrinsics (pixel centres at whole numbers) of the image scaled by ``s``."""
    fx, fy, cx, cy = K
    return np.array([fx * s, fy * s, (cx + 0.5) * s - 0.5, (cy + 0.5) * s - 0.5])


def lift(rgb: np.ndarray, depth: np.ndarray, K: np.ndarray, c2w: np.ndarray, valid: np.ndarray | None = None) -> Cloud:
    """One point per pixel with a depth, at the pixel's footprint (``c2w``: OpenCV axes)."""
    h, w = depth.shape
    v, u = np.mgrid[0:h, 0:w]
    ok = np.isfinite(depth) & (depth > 0.05) & (depth < MAX_DEPTH_M)
    if valid is not None:
        ok &= valid
    fx, fy, cx, cy = K
    z = depth[ok]
    cam = np.stack([(u[ok] - cx) / fx * z, (v[ok] - cy) / fy * z, z], 1)
    xyz = cam @ c2w[:3, :3].T + c2w[:3, 3]
    return Cloud(xyz.astype(np.float32), rgb[ok], (z / fx).astype(np.float32))


def render(cloud: Cloud, c2w: np.ndarray, K: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """The cloud seen from ``c2w`` (OpenCV axes) with intrinsics ``K`` at ``size`` (width, height):
    each point a square of its projected size, the nearest point winning each pixel. Returns
    the image (uint8) and which pixels something covers."""
    import torch

    dev = device()
    w, h = size
    w2c = np.linalg.inv(c2w)
    xyz = torch.as_tensor(cloud.xyz, dtype=torch.float32, device=dev)
    cam = xyz @ torch.as_tensor(w2c[:3, :3].T, dtype=torch.float32, device=dev) + torch.as_tensor(
        w2c[:3, 3], dtype=torch.float32, device=dev
    )
    z = cam[:, 2]
    fx, fy, cx, cy = (float(k) for k in K)
    zs = torch.where(z > 0.05, z, torch.ones_like(z))
    u = torch.round(cam[:, 0] / zs * fx + cx).long()
    v = torch.round(cam[:, 1] / zs * fy + cy).long()
    footprint = torch.as_tensor(cloud.size_m, dtype=torch.float32, device=dev) * fx / zs
    # A square of 2r + 1 px covers the footprint (the tolerance keeps a 1 px footprint at one pixel).
    radius = torch.clamp(torch.ceil((footprint - 1) / 2 - 0.01), 0, MAX_RADIUS_PX).long()
    keep = (
        (z > 0.05) & (u >= -MAX_RADIUS_PX) & (u < w + MAX_RADIUS_PX) & (v >= -MAX_RADIUS_PX) & (v < h + MAX_RADIUS_PX)
    )
    zbuf = torch.full((h * w,), float("inf"), device=dev)
    parts = []
    for r in range(MAX_RADIUS_PX + 1):
        sel = torch.nonzero(keep & (radius == r)).squeeze(1)
        if not len(sel):
            continue
        d = torch.arange(-r, r + 1, device=dev)
        dy, dx = torch.meshgrid(d, d, indexing="ij")
        pu = (u[sel, None] + dx.reshape(1, -1)).reshape(-1)
        pv = (v[sel, None] + dy.reshape(1, -1)).reshape(-1)
        who = sel[:, None].expand(-1, dx.numel()).reshape(-1)
        inside = (pu >= 0) & (pu < w) & (pv >= 0) & (pv < h)
        pix, who = (pv * w + pu)[inside], who[inside]
        zbuf.scatter_reduce_(0, pix, z[who], reduce="amin")
        parts.append((pix, who))
    image = torch.zeros((h * w, 3), dtype=torch.uint8, device=dev)
    colours = torch.as_tensor(cloud.rgb, dtype=torch.uint8, device=dev)
    for pix, who in parts:
        win = z[who] <= zbuf[pix] + 1e-6
        image[pix[win]] = colours[who[win]]
    covered = torch.isfinite(zbuf)
    return image.reshape(h, w, 3).cpu().numpy(), covered.reshape(h, w).cpu().numpy()


def psnr(a: np.ndarray, b: np.ndarray, mask: np.ndarray | None = None) -> float | None:
    """PSNR (dB) of two uint8 images over ``mask``'s pixels (all when None)."""
    diff = a.astype(np.float64) - b.astype(np.float64)
    if mask is not None:
        if mask.sum() < 100:
            return None
        diff = diff[mask]
    mse = float(np.mean(diff**2))
    return round(10 * math.log10(255.0**2 / max(mse, 1e-10)), 2)


def depth_vs_lidar(depth: np.ndarray, lidar: np.ndarray, confidence: np.ndarray) -> dict | None:
    """A model's depth (any size) against the LiDAR's (256 x 192), where the LiDAR is reliable."""
    import torch

    small = torch.nn.functional.interpolate(torch.as_tensor(depth, dtype=torch.float32)[None, None],
                                            size=lidar.shape, mode="area")[0, 0].numpy()  # fmt: skip
    good = (confidence >= 1) & (lidar > 0.2) & (lidar < LIDAR_RANGE_M) & np.isfinite(small) & (small > 0)
    if good.sum() < 200:
        return None
    ratio = small[good] / lidar[good]
    scale = float(np.median(ratio))
    return {
        "abs_rel": round(float(np.median(np.abs(ratio - 1))), 3),
        "abs_rel_scaled": round(float(np.median(np.abs(ratio / scale - 1))), 3),  # shape alone, the scale fixed
        "scale": round(scale, 3),
        "delta_1_25": round(float(np.mean(np.maximum(ratio, 1 / ratio) < 1.25)), 3),
        "pixels": int(good.sum()),
    }


def depth_model(name: str) -> DepthModel:
    """A metric depth model from Hugging Face (``DEPTH_MODELS``), loaded once."""
    import torch
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    dev = device()
    processor = AutoImageProcessor.from_pretrained(DEPTH_MODELS[name])
    model = AutoModelForDepthEstimation.from_pretrained(DEPTH_MODELS[name]).to(dev).eval()

    def run(rgb: np.ndarray, K: np.ndarray) -> np.ndarray:
        h, w = rgb.shape[:2]
        inputs = processor(images=rgb, return_tensors="pt").to(dev)
        with torch.inference_mode():
            out = model(**inputs)
        raw = out.predicted_depth.float()
        if raw.dim() == 2:
            raw = raw[None]
        resized = torch.nn.functional.interpolate(raw[:, None], size=(h, w), mode="bilinear", align_corners=False)[0, 0]
        if name == "depth-pro":
            # Canonical inverse depth: the depth is f / (it x width), with the focal length the
            # photo's own (glasses are calibrated), not the one the model guesses.
            fov = getattr(out, "field_of_view", None)
            if fov is not None:
                run.focal_ratio = float(0.5 * w / math.tan(0.5 * math.radians(float(fov.reshape(-1)[0])))) / float(K[0])
            return (float(K[0]) / (torch.clamp(resized, min=1e-4) * w)).cpu().numpy()
        return resized.cpu().numpy()

    run.focal_ratio = None
    return run


class Sharp:
    """SHARP's Gaussians from one photo, in the camera's frame (OpenCV axes, metres)."""

    def __init__(self) -> None:
        import torch
        from sharp.models import PredictorParams, create_predictor

        self.dev = device()
        state = torch.hub.load_state_dict_from_url(SHARP_URL, progress=False, map_location="cpu")
        self.model = create_predictor(PredictorParams())
        self.model.load_state_dict(state)
        self.model.eval().to(self.dev)

    def cloud(self, rgb: np.ndarray, f_px: float, c2w: np.ndarray) -> Cloud:
        from sharp.cli.predict import predict_image
        from sharp.utils.color_space import linearRGB2sRGB

        g = predict_image(self.model, rgb, f_px, self.dev)
        keep = (g.opacities[0] >= SHARP_OPACITY).cpu().numpy()
        means = g.mean_vectors[0].cpu().numpy()[keep]
        colours = linearRGB2sRGB(g.colors[0]).clamp(0, 1).cpu().numpy()[keep]
        sizes = 2 * g.singular_values[0].mean(-1).cpu().numpy()[keep]  # a disc across one sigma each way
        xyz = means @ c2w[:3, :3].T + c2w[:3, 3]
        self.means = means  # in the camera's frame, for its depth
        return Cloud(xyz.astype(np.float32), np.round(colours * 255).astype(np.uint8), sizes.astype(np.float32))


def depth_of_points(cam: np.ndarray, K: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """The nearest depth of camera-frame points (OpenCV axes) at each pixel of ``size``
    (width, height) with intrinsics ``K``; NaN where none falls."""
    w, h = size
    z = cam[:, 2]
    front = z > 0.05
    u = np.round(cam[front, 0] / z[front] * K[0] + K[2]).astype(int)
    v = np.round(cam[front, 1] / z[front] * K[1] + K[3]).astype(int)
    inside = (u >= 0) & (u < w) & (v >= 0) & (v < h)
    out = np.full(h * w, np.inf)
    np.minimum.at(out, v[inside] * w + u[inside], z[front][inside])
    out[~np.isfinite(out)] = np.nan
    return out.reshape(h, w)


@dataclass
class Scene:
    """The scan's truth: per video frame its time, pose (OpenCV axes), intrinsics at the video's
    size, distance walked, and depth file number."""

    t: np.ndarray
    c2w: np.ndarray
    K: np.ndarray
    walked: np.ndarray
    frame: np.ndarray
    width: int
    height: int

    @property
    def work(self) -> tuple[int, int]:
        return WIDTH, round(self.height * WIDTH / self.width)

    def K_work(self, i: int) -> np.ndarray:
        return scale_K(self.K[i], WIDTH / self.width)

    def top(self, i: np.ndarray | int) -> np.ndarray:
        """Seen from above, as ``align.georeference`` places a walk: x, and -z (ARKit, y up)."""
        p = self.c2w[i, :3, 3]
        return np.stack([p[..., 0], -p[..., 2]], -1)


def read_scene(capture: Path, video: Path) -> Scene:
    truth = simulate.read_truth(capture, video)
    odo = dataset.read_odometry(capture, truth.width, truth.height)
    n = len(truth.t)
    step = np.linalg.norm(np.diff(truth.c2w[:, :3, 3], axis=0), axis=1)
    walked = np.concatenate([[0.0], np.cumsum(step)])
    return Scene(truth.t, truth.c2w, truth.K, walked, odo.frame[:n], truth.width, truth.height)


def pick_photos(scene: Scene, count: int, sharp: np.ndarray | None) -> list[int]:
    """``count`` photos spread along the walk, each the sharpest frame within ``STILL_S`` of its
    place, at least ``MARGIN_M`` from either end."""
    if scene.walked[-1] < 2 * MARGIN_M:
        return []
    picks = []
    for d in np.linspace(MARGIN_M, scene.walked[-1] - MARGIN_M, count):
        i = int(np.searchsorted(scene.walked, d))
        if sharp is not None:
            lo = int(np.searchsorted(scene.t, scene.t[i] - simulate.STILL_S))
            hi = int(np.searchsorted(scene.t, scene.t[i] + simulate.STILL_S, side="right"))
            i = lo + int(np.argmax(sharp[lo:hi]))
        picks.append(i)
    return sorted(set(picks))


def ahead(scene: Scene, i: int, metres: float) -> int | None:
    """The first frame at least ``metres`` further along the walk than frame ``i``."""
    j = int(np.searchsorted(scene.walked, scene.walked[i] + metres))
    return j if j < len(scene.t) else None


def lidar_of(capture: Path, scene: Scene, frames: list[int]) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """LiDAR depth (m) and confidence (256 x 192) of the given video frames, where they exist."""
    paths = {i: capture / "depth" / f"{int(scene.frame[i]):06d}.png" for i in frames}
    have = [i for i in frames if paths[i].is_file()]
    if not have:
        return {}
    depth = dataset.read_maps([paths[i] for i in have], "gray16le", "<u2").astype(np.float32) / 1000.0
    conf_paths = [capture / "confidence" / f"{int(scene.frame[i]):06d}.png" for i in have]
    if all(p.is_file() for p in conf_paths):
        conf = dataset.read_maps(conf_paths, "gray", "u1")
    else:
        conf = np.full(depth.shape, 2, np.uint8)
    return {i: (depth[k], conf[k]) for k, i in enumerate(have)}


def upsample(depth: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """A depth map to (width, height), nearest (no depths invented between surfaces)."""
    w, h = size
    v = np.minimum((np.arange(h) + 0.5) * depth.shape[0] / h, depth.shape[0] - 1).astype(int)
    u = np.minimum((np.arange(w) + 0.5) * depth.shape[1] / w, depth.shape[1] - 1).astype(int)
    return depth[v][:, u]


def gps_prior(scene: Scene, scan: np.ndarray, t0: float, fixes: list[Fix]) -> Callable[[float], np.ndarray] | None:
    """Where the GPS says the camera was at a time (seconds from the video's start), in the
    scan's top view: the scan's path fitted to the GPS (``align.georeference``: clock offset and
    a 2D similarity), then the fix at that time carried back through the fit."""
    level = scene.c2w[scan, :3, 3]
    fit = align.georeference(level, scene.t[scan], t0, fixes)
    if fit is None:
        return None
    near = [f for f in fixes if abs(f.t.timestamp() - (t0 + scene.t[scan[0]])) < 3600]
    lat0, lon0 = near[0].lat, near[0].lon
    ft = np.array([f.t.timestamp() for f in near])
    en = np.array([local(f.lat, f.lon, lat0, lon0) for f in near])
    times = t0 + fit["offset_s"] + scene.t[scan]
    gps = np.column_stack([np.interp(times, ft, en[:, 0]), np.interp(times, ft, en[:, 1])])
    s, R, shift = align.umeyama(scene.top(scan), gps)

    def prior(t: float) -> np.ndarray:
        g = np.array(
            [np.interp(t0 + fit["offset_s"] + t, ft, en[:, 0]), np.interp(t0 + fit["offset_s"] + t, ft, en[:, 1])]
        )
        return R.T @ (g - shift) / s

    prior.fit = fit  # type: ignore[attr-defined]
    return prior


def pose_error(est: np.ndarray, true: np.ndarray) -> tuple[float, float]:
    """Position (m) and rotation (degrees) between two camera-to-world poses."""
    dp = float(np.linalg.norm(est[:3, 3] - true[:3, 3]))
    R = est[:3, :3].T @ true[:3, :3]
    angle = math.degrees(math.acos(float(np.clip((np.trace(R) - 1) / 2, -1, 1))))
    return dp, angle


# PnP variants: which scan points, and the RANSAC threshold (px at the working size). COLMAP's
# default is 12 px; the scan's depth beyond the LiDAR's range is ARKit's estimate.
VARIANTS = {"all_12px": (False, 12.0), "all_4px": (False, 4.0), "lidar_4px": (True, 4.0)}
PRIMARY = ("lidar_4px", "all_4px")  # the first that places the photo is reported first


def correspondences(found: tuple, scan_found: list[tuple], scan_xyz: list[np.ndarray], scan_reliable: list[np.ndarray],
                    candidates: list[int], features: object) -> tuple[np.ndarray, np.ndarray, np.ndarray]:  # fmt: skip
    """A photo's keypoints matched with the candidate scan frames' (``scan_xyz[j][k]``: keypoint
    k's world point, NaN without depth): 2D points, their 3D points, and whether each 3D point is
    from the LiDAR's reliable depth."""
    p2, p3, ok3 = [], [], []
    query = features.pixels(found)  # type: ignore[attr-defined]
    for j in candidates:
        index = features.match(found, scan_found[j])  # type: ignore[attr-defined]
        if not len(index):
            continue
        xyz = scan_xyz[j][index[:, 1]]
        ok = np.isfinite(xyz).all(1)
        p2.append(query[index[ok, 0]])
        p3.append(xyz[ok])
        ok3.append(scan_reliable[j][index[ok, 1]])
    if not p2:
        return np.zeros((0, 2)), np.zeros((0, 3)), np.zeros(0, bool)
    return np.concatenate(p2).astype(np.float64), np.concatenate(p3).astype(np.float64), np.concatenate(ok3)


def pnp(pts2: np.ndarray, pts3: np.ndarray, K: np.ndarray, size: tuple[int, int], max_error: float) -> dict:
    """The camera's pose from 2D-3D matches: LO-RANSAC with ``max_error`` px, then refined
    (COLMAP). Returns the pose (OpenCV axes) and the inliers."""
    import pycolmap

    if len(pts2) < 12:
        return {"inliers": 0}
    fx, fy, cx, cy = K
    # COLMAP's pixel centres are at .5.
    camera = pycolmap.Camera(model="PINHOLE", width=size[0], height=size[1], params=[fx, fy, cx + 0.5, cy + 0.5])
    options = pycolmap.AbsolutePoseEstimationOptions()
    options.ransac.max_error = max_error
    result = pycolmap.estimate_and_refine_absolute_pose(pts2 + 0.5, pts3, camera, options)
    if result is None:
        return {"inliers": 0}
    w2c = np.eye(4)
    w2c[:3, :4] = np.asarray(result["cam_from_world"].matrix())
    return {"inliers": int(result["num_inliers"]), "c2w": np.linalg.inv(w2c)}


def _save(path: Path, *images: np.ndarray) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.concatenate(images, 1)).save(path, quality=88)


def _median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return round(float(np.median(values)), 3) if values else None


def run(capture: Path, video: Path, work: Path, *, fixes: list[Fix] | None = None, t0: float | None = None,
        photos: int = PHOTOS, methods: tuple[str, ...] = ("flat", "lidar", "depth-pro", "dav2-metric", "sharp"),
        locate_too: bool = True, models: dict | None = None, features: object = None,
        on_result: Callable[[dict], None] | None = None, say: Callable[[str], None] = print) -> dict:  # fmt: skip
    """Both tests on one scan. ``models``: depth models or a SHARP to use instead of loading them
    (tests); ``features``: ``learned.Features`` likewise."""
    import torch

    scene = read_scene(capture, video)
    size = scene.work
    say(f"{len(scene.t)} frames, {scene.walked[-1]:.1f} m walked; scoring sharpness")
    sharp_scores = dataset.sharpness(video, scene.width, scene.height)[: len(scene.t)]
    chosen = pick_photos(scene, photos, sharp_scores)
    report: dict = {"frames": len(scene.t), "path_m": round(float(scene.walked[-1]), 1), "size": list(size),
                    "ahead_m": list(AHEAD_M), "photos": [], "lift": {}, "locate": None}  # fmt: skip
    if not chosen:
        report["error"] = "walk too short"
        return report
    targets = {(i, d): ahead(scene, i, d) for i in chosen for d in AHEAD_M}
    needed = sorted(set(chosen) | {j for j in targets.values() if j is not None})
    small, found_small = dataset.frames_at(video, np.array(needed), size)
    frames = {i: small[k] for k, i in enumerate(needed) if found_small[k]}
    full, found_full = dataset.frames_at(video, np.array(chosen), (scene.width, scene.height))
    photo_full = {i: full[k] for k, i in enumerate(chosen) if found_full[k]}
    lidar = lidar_of(capture, scene, chosen)
    for i in chosen:
        report["photos"].append({"frame": int(i), "t": round(float(scene.t[i]), 2), "walked_m": round(float(scene.walked[i]), 1),
                                 "lidar": i in lidar})  # fmt: skip
    say(f"{len(chosen)} photos; rendering each 0.25-4 m further along the walk")
    models = dict(models or {})

    # A. Lift.
    for method in methods:
        started = time.monotonic()
        # A variant per method, and for the models also their depth scaled to the LiDAR's (their
        # shape alone; glasses could take the scale from GPS or from a scan of the place).
        rows: dict[str, dict[float, list[dict]]] = {}
        depth_rows = []
        focal_ratios = []
        offsets: dict[int, list] = {}
        try:
            if method in DEPTH_MODELS and method not in models:
                say(f"Loading {method}")
                models[method] = depth_model(method)
            if method == "sharp" and "sharp" not in models:
                say("Loading SHARP")
                models["sharp"] = Sharp()
        except Exception as error:  # noqa: BLE001 - a model that cannot load is reported, not fatal
            report["lift"][method] = {
                "label": METHODS.get(method, method),
                "error": f"{type(error).__name__}: {error}"[:400],
            }
            say(f"{method}: could not load ({type(error).__name__})")
            continue
        for k, i in enumerate(chosen):
            if i not in frames or i not in photo_full:
                continue
            photo = frames[i]
            K = scene.K_work(i)
            clouds: dict[str, Cloud | None] = {}
            if method == "flat":
                clouds["flat"] = None
            elif method == "lidar":
                if i not in lidar:
                    continue
                clouds["lidar"] = lift(photo, upsample(lidar[i][0], size), K, scene.c2w[i])
                # Are the video's frames and ARKit's poses in step? The frame 0.5 m on, drawn with
                # the poses of the frames around it: the best should be its own.
                j = targets[(i, 0.5)]
                if j is not None and j in frames:
                    for shift in OFFSETS:
                        if 0 <= j + shift < len(scene.t):
                            image, covered = render(
                                clouds["lidar"], scene.c2w[j + shift], scene.K_work(j + shift), size
                            )
                            offsets.setdefault(shift, []).append(psnr(image, frames[j], covered))
            elif method in DEPTH_MODELS:
                depth_full = models[method](photo_full[i], scene.K[i])
                if getattr(models[method], "focal_ratio", None):
                    focal_ratios.append(models[method].focal_ratio)
                depth = torch.nn.functional.interpolate(torch.as_tensor(depth_full, dtype=torch.float32)[None, None],
                                                        size=(size[1], size[0]), mode="area")[0, 0].numpy()  # fmt: skip
                clouds[method] = lift(photo, depth, K, scene.c2w[i])
                compared = depth_vs_lidar(depth_full, *lidar[i]) if i in lidar else None
                if compared:
                    depth_rows.append(compared)
                    clouds[f"{method}+lidar-scale"] = lift(photo, depth / compared["scale"], K, scene.c2w[i])
            elif method == "sharp":
                sharp = models["sharp"]
                clouds["sharp"] = sharp.cloud(photo_full[i], float(scene.K[i][0]), scene.c2w[i])
                if i in lidar:
                    small = (dataset.DEPTH_W, dataset.DEPTH_H)
                    seen = depth_of_points(sharp.means, scale_K(scene.K[i], dataset.DEPTH_W / scene.width), small)
                    compared = depth_vs_lidar(np.nan_to_num(seen, nan=0.0), *lidar[i])
                    if compared:
                        depth_rows.append(compared)
                        c = clouds["sharp"]
                        centre = scene.c2w[i, :3, 3]
                        scaled = Cloud(((c.xyz - centre) / compared["scale"] + centre).astype(np.float32), c.rgb,
                                       c.size_m / compared["scale"])  # fmt: skip
                        clouds["sharp+lidar-scale"] = scaled
            for name, cloud in clouds.items():
                table = rows.setdefault(name, {d: [] for d in AHEAD_M})
                for d in AHEAD_M:
                    j = i if d == 0 else targets[(i, d)]
                    if j is None or j not in frames:
                        continue
                    truth = frames[j]
                    if cloud is None:
                        table[d].append({"coverage": 1.0, "psnr": psnr(photo, truth), "psnr_flat": psnr(photo, truth)})
                        continue
                    image, covered = render(cloud, scene.c2w[j], scene.K_work(j), size)
                    table[d].append({"coverage": round(float(covered.mean()), 3), "psnr": psnr(image, truth, covered),
                                     "psnr_flat": psnr(photo, truth, covered)})  # fmt: skip
                    if d in (1.0, 4.0):
                        _save(work / "lift" / f"{k:02d}_{name}_{d:g}m.jpg", photo, image, truth)
        seconds = round((time.monotonic() - started) / max(1, len(chosen)), 2)
        depth_rows = [r for r in depth_rows if r]
        for name, table in rows.items():
            summary = {
                "label": METHODS.get(name, f"{METHODS.get(method, method)}, depth scaled to the LiDAR's"),
                "seconds_per_photo": seconds,
                "ahead": {f"{d:g}": {"photos": len(r), "coverage": _median([x["coverage"] for x in r]),
                                     "psnr": _median([x["psnr"] for x in r]),
                                     "psnr_flat": _median([x["psnr_flat"] for x in r])}
                          for d, r in table.items()},
            }  # fmt: skip
            if depth_rows and name == method:
                summary["depth_vs_lidar"] = {
                    key: _median([r[key] for r in depth_rows])
                    for key in ("abs_rel", "abs_rel_scaled", "scale", "delta_1_25")
                }
            if focal_ratios and name == method:
                summary["focal_guessed_over_true"] = _median(focal_ratios)
            if offsets and name == "lidar":
                summary["pose_offset_frames"] = {str(k): _median(v) for k, v in sorted(offsets.items())}
            report["lift"][name] = summary
        if not rows:
            report["lift"][method] = {"label": METHODS.get(method, method), "error": "no photo could be lifted"}
        if method in DEPTH_MODELS or method == "sharp":
            models.pop(method, None)  # free the GPU for the next one
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        if on_result:
            on_result(report)
        say(f"{method}: done ({seconds} s a photo)")

    # B. Locate in the scan.
    if locate_too:
        report["locate"] = _locate(scene, capture, video, chosen, frames, size, fixes, t0, features, work, say)
        if on_result:
            on_result(report)
    return report


def _locate(scene: Scene, capture: Path, video: Path, chosen: list[int], frames: dict, size: tuple[int, int],
            fixes: list[Fix] | None, t0: float | None, features: object, work: Path,
            say: Callable[[str], None]) -> dict:  # fmt: skip
    from . import learned

    marks = np.arange(0.0, scene.walked[-1], MAP_STEP_M)
    scan = np.unique(np.clip(np.searchsorted(scene.walked, marks), 0, len(scene.t) - 1))
    scan = scan[scene.t[scan] >= simulate.SETTLE_S]
    depth = lidar_of(capture, scene, [int(i) for i in scan])
    scan = np.array([i for i in scan if int(i) in depth])
    if len(scan) < 10:
        return {"error": f"the scan has {len(scan)} frames with depth"}
    say(f"The scan: {len(scan)} frames every {MAP_STEP_M} m with depth; finding their keypoints")
    images, found = dataset.frames_at(video, scan, size)
    features = features or learned.Features()
    started = time.monotonic()
    scan_found, scan_xyz, scan_reliable = [], [], []
    for k, i in enumerate(scan):
        item = features.detect(images[k])  # type: ignore[attr-defined]
        scan_found.append(item)
        px = features.pixels(item)  # type: ignore[attr-defined]
        d, conf = depth[int(i)]
        s = d.shape[1] / size[0]
        u = np.clip(np.round((px[:, 0] + 0.5) * s - 0.5).astype(int), 0, d.shape[1] - 1)
        v = np.clip(np.round((px[:, 1] + 0.5) * s - 0.5).astype(int), 0, d.shape[0] - 1)
        z = d[v, u]
        fx, fy, cx, cy = scene.K_work(int(i))
        cam = np.stack([(px[:, 0] - cx) / fx * z, (px[:, 1] - cy) / fy * z, z], 1)
        xyz = cam @ scene.c2w[i, :3, :3].T + scene.c2w[i, :3, 3]
        xyz[(z < 0.2) | (z > 15.0)] = np.nan
        scan_xyz.append(xyz)
        scan_reliable.append((conf[v, u] >= 1) & (z < LIDAR_RANGE_M))
    keypoint_s = time.monotonic() - started
    prior = gps_prior(scene, scan, t0, fixes) if fixes and t0 is not None else None
    rng = np.random.default_rng(7)
    rows = []
    for k, i in enumerate(chosen):
        if i not in frames:
            continue
        started = time.monotonic()
        guess = prior(float(scene.t[i])) if prior is not None else scene.top(i) + rng.normal(0, GPS_SIGMA_M, 2)
        away = np.abs(scene.t[scan] - scene.t[i]) >= EXCLUDE_S
        dist = np.linalg.norm(scene.top(scan) - guess, axis=1)
        order = [int(j) for j in np.argsort(dist) if away[j] and dist[j] <= PRIOR_RADIUS_M][:NEAREST]
        K = scene.K_work(i)
        found_q = features.detect(frames[i])  # type: ignore[attr-defined]
        pts2, pts3, reliable = correspondences(found_q, scan_found, scan_xyz, scan_reliable, order, features)
        row = {"frame": int(i), "candidates": len(order), "matches": len(pts2), "reliable": int(reliable.sum()),
               "prior_error_m": round(float(np.linalg.norm(guess - scene.top(i))), 2)}  # fmt: skip
        poses: dict[str, np.ndarray] = {}
        for variant, (only_reliable, max_error) in VARIANTS.items():
            keep = reliable if only_reliable else np.ones(len(pts2), bool)
            got = pnp(pts2[keep], pts3[keep], K, size, max_error)
            cell = {"inliers": got["inliers"]}
            if "c2w" in got:
                poses[variant] = got["c2w"]
                dp, dr = pose_error(got["c2w"], scene.c2w[i])
                cell |= {"position_m": round(dp, 3), "rotation_deg": round(dr, 2)}
            row[variant] = cell
        chosen_variant = next((v for v in PRIMARY if v in poses), None)
        if chosen_variant is not None:
            pose = poses[chosen_variant]
            row |= row[chosen_variant] | {"variant": chosen_variant}
            # What the scan shows from there: the nearest scan frames' depth, drawn at the pose found.
            near = np.argsort(np.linalg.norm(scene.c2w[scan, :3, 3] - pose[:3, 3], axis=1))[:3]
            clouds = [lift(images[n], upsample(depth[int(scan[n])][0], size), scene.K_work(int(scan[n])), scene.c2w[scan[n]])
                      for n in near]  # fmt: skip
            cloud = Cloud(*(np.concatenate([getattr(c, f) for c in clouds]) for f in ("xyz", "rgb", "size_m")))
            image, covered = render(cloud, pose, K, size)
            at_truth, covered_truth = render(cloud, scene.c2w[i], K, size)
            row |= {"scan_coverage": round(float(covered.mean()), 3), "psnr_scan": psnr(image, frames[i], covered),
                    "psnr_scan_at_truth": psnr(at_truth, frames[i], covered_truth)}  # fmt: skip
            diff = np.abs(image.astype(int) - frames[i].astype(int)).clip(0, 255).astype(np.uint8)
            _save(work / "locate" / f"{k:02d}.jpg", frames[i], image, diff)
        row["seconds"] = round(time.monotonic() - started, 2)
        rows.append(row)
        say(f"photo {k + 1}: " + ", ".join(f"{v} {row[v].get('position_m', '-')} m {row[v].get('rotation_deg', '-')} deg"
                                           for v in VARIANTS) + f"; {row['matches']} matches")  # fmt: skip
    placed = [r for r in rows if "position_m" in r]

    def within(m: float, deg: float, cells: list[dict] | None = None) -> float:
        cells = [r for r in (cells if cells is not None else placed) if "position_m" in r]
        return round(sum(r["position_m"] <= m and r["rotation_deg"] <= deg for r in cells) / max(1, len(rows)), 3)

    variants = {
        v: {"median_position_m": _median([r[v].get("position_m") for r in rows]),
            "median_rotation_deg": _median([r[v].get("rotation_deg") for r in rows]),
            "within_0.5m_5deg": within(0.5, 5, [r[v] for r in rows]),
            "within_0.25m_2deg": within(0.25, 2, [r[v] for r in rows])}
        for v in VARIANTS
    }  # fmt: skip

    return {
        "scan_frames": len(scan), "scan_step_m": MAP_STEP_M, "exclude_s": EXCLUDE_S,
        "prior": "gps" if prior is not None else f"simulated ({GPS_SIGMA_M} m)",
        "gps_fit": {k: v for k, v in (getattr(prior, "fit", None) or {}).items() if k != "latlon"} or None,
        "keypoints_s": round(keypoint_s, 1), "primary": list(PRIMARY), "variants": variants,
        "median_prior_error_m": _median([r["prior_error_m"] for r in rows]),
        "placed": len(placed), "photos": len(rows),
        "median_position_m": _median([r["position_m"] for r in placed]),
        "median_rotation_deg": _median([r["rotation_deg"] for r in placed]),
        "within": {"0.25m_2deg": within(0.25, 2), "0.5m_5deg": within(0.5, 5), "1m_10deg": within(1.0, 10)},
        "median_psnr_scan": _median([r.get("psnr_scan") for r in placed]),
        "median_psnr_scan_at_truth": _median([r.get("psnr_scan_at_truth") for r in placed]),
        "median_seconds": _median([r["seconds"] for r in rows]),
        "per_photo": rows,
    }  # fmt: skip

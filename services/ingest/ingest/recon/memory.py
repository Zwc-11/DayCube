"""Memories: one photo you can step into (docs/memories.md).

A photo becomes a small 3D scene with SHARP (Apple, research licence: personal and research use,
not a product): one pass of a network predicts 3D Gaussians, in metres, for what the photo shows,
in about 6 s on the RTX 5070. The scene holds up for steps of a metre or so around where the photo
was taken (``docs/glasses.md``, "One photo"): enough to lean in, look a little to the side and
see the depth, which is the point.

Each memory is a folder in its day, ``vault/<day>/memories/<id>/``:

- ``photo.jpg``: the photo, at most 1600 px on its long side (the viewer fades from it into 3D);
- ``memory.splat``: the Gaussians as the viewer's 32-byte splat records, in the viewer's axes (x
  right, y up, z towards the viewer: the photo's camera at the origin looking down -z);
- ``memory.json``: what the viewer needs (size, focal length, how far the scene is, counts);
- ``original.<ext>``: the file as it came, for one dropped in the inbox.

Photos come from ``vault/inbox/memories`` (any JPEG, PNG or iPhone HEIC; the day from its EXIF
time, else the file's), or from the sharpest frames of a day's scans (``--scan DATE``, or
``--scan latest``: the newest day with a scan whose capture is still there). The receiver runs this
on its own (``capture/memories.py``); a photo that cannot be read is moved to
``vault/inbox/memories/failed`` with the reason in ``<name>.txt``.

    uv run --project services/recon python -m ingest.recon.memory [--inbox] [--scan latest]
        [--count 6] [--if-none] [photo ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np

from ..splat import export

PHOTO_SIDE = 1600
OPACITY = 0.05  # Gaussians fainter than this are left out
CAP = 1_500_000  # at most this many, the most visible first (48 MB)
SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
SETTLED_S = 10.0  # a photo changed more recently than this may still be being copied in: left for later

# OpenCV camera axes (SHARP's: x right, y down, z forward) to the viewer's (y up, z back): a half
# turn about x. A Gaussian's rotation q becomes F q, with F = (w 0, x 1, y 0, z 0).
FLIP = np.array([1.0, -1.0, -1.0])


def to_viewer(means: np.ndarray, quats_wxyz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Positions and rotations from OpenCV camera axes to the viewer's."""
    w, x, y, z = quats_wxyz.T
    return means * FLIP, np.stack([-x, w, -z, y], 1)


def records(means: np.ndarray, scales: np.ndarray, quats_wxyz: np.ndarray, rgb: np.ndarray, alpha: np.ndarray,
            cap: int = CAP) -> np.ndarray:  # fmt: skip
    """SHARP's Gaussians (OpenCV axes; ``rgb`` sRGB in [0, 1]) as the viewer's splat records."""
    keep = (alpha >= OPACITY) & np.isfinite(means).all(1) & np.isfinite(scales).all(1) & (means[:, 2] > 0)
    position, quat = to_viewer(means[keep], quats_wxyz[keep])
    return export._records(position.astype(np.float32), scales[keep].astype(np.float32), rgb[keep], alpha[keep],
                           quat, cap)  # fmt: skip


class Sharp:
    """SHARP loaded once, on the GPU when there is one."""

    URL = "https://ml-site.cdn-apple.com/models/sharp/sharp_2572gikvuh.pt"

    def __init__(self) -> None:
        import torch
        from sharp.models import PredictorParams, create_predictor

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        state = torch.hub.load_state_dict_from_url(self.URL, progress=False, map_location="cpu")
        self.model = create_predictor(PredictorParams())
        self.model.load_state_dict(state)
        self.model.eval().to(self.device)

    def __call__(self, rgb: np.ndarray, f_px: float) -> dict:
        """Gaussians in the camera's frame (OpenCV axes, metres): means, scales (sigma), rotations
        (w x y z), sRGB colours and opacities, as arrays."""
        from sharp.cli.predict import predict_image
        from sharp.utils.color_space import linearRGB2sRGB

        g = predict_image(self.model, rgb, f_px, self.device)
        return {
            "means": g.mean_vectors[0].cpu().numpy(),
            "scales": g.singular_values[0].cpu().numpy(),
            "quats": g.quaternions[0].cpu().numpy(),
            "rgb": linearRGB2sRGB(g.colors[0]).clamp(0, 1).cpu().numpy(),
            "alpha": g.opacities[0].cpu().numpy(),
        }


def load_photo(path: Path) -> tuple[np.ndarray, float, datetime]:
    """The photo upright (RGB), its focal length in pixels (from EXIF; a 30 mm-equivalent lens
    when it has none) and when it was taken (EXIF, else the file's time)."""
    from PIL import ExifTags, Image, ImageOps

    if path.suffix.lower() in {".heic", ".heif"}:
        import pillow_heif

        pillow_heif.register_heif_opener()
    image = Image.open(path)
    exif = image.getexif()
    sub = exif.get_ifd(ExifTags.IFD.Exif) if exif else {}
    image = ImageOps.exif_transpose(image).convert("RGB")
    rgb = np.asarray(image)
    f35 = sub.get(ExifTags.Base.FocalLengthIn35mmFilm) or 0
    if not f35:
        focal = float(sub.get(ExifTags.Base.FocalLength) or 0)
        f35 = focal * 8.4 if 0 < focal < 10 else focal or 30.0  # a phone's real focal length: about 8.4x
    f_px = float(f35) / 36.0 * max(rgb.shape[:2])  # 35 mm film is 36 mm across its long side
    taken = sub.get(ExifTags.Base.DateTimeOriginal) or exif.get(ExifTags.Base.DateTime) if exif else None
    try:
        when = datetime.strptime(str(taken), "%Y:%m:%d %H:%M:%S") if taken else None
    except ValueError:
        when = None
    return rgb, f_px, when or datetime.fromtimestamp(path.stat().st_mtime)


def make(rgb: np.ndarray, f_px: float, folder: Path, *, predict: Callable[[np.ndarray, float], dict],
         source: dict | None = None, world: Callable[[dict], dict | None] | None = None) -> dict:  # fmt: skip
    """One memory from an RGB photo with its focal length (pixels at that size), into ``folder``.
    ``world``: for a photo whose place was scanned, where it stands in the scan (``pose``, the
    camera to the scan's world, OpenGL axes) and ``scale`` (the scan's metres per SHARP metre),
    from the predicted Gaussians."""
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    h, w = rgb.shape[:2]
    started = time.monotonic()
    g = predict(rgb, f_px)
    seconds = time.monotonic() - started
    out = records(g["means"], g["scales"], g["quats"], g["rgb"], g["alpha"])
    (folder / "memory.splat").write_bytes(out.tobytes())
    photo = Image.fromarray(rgb)
    if max(w, h) > PHOTO_SIDE:
        s = PHOTO_SIDE / max(w, h)
        photo = photo.resize((round(w * s), round(h * s)), Image.Resampling.LANCZOS)
    photo.save(folder / "photo.jpg", quality=90)
    depth = -out["position"][:, 2]
    near_mask = out["rgba"][:, 3] >= 128
    meta = {
        "photo": "photo.jpg", "splat": "memory.splat", "width": int(w), "height": int(h),
        "fx": round(float(f_px), 2), "fov_y_deg": round(float(np.degrees(2 * np.arctan(h / 2 / f_px))), 2),
        "splats": int(len(out)), "predicted": int(len(g["alpha"])),
        # How far the scene is: the viewer moves within a fraction of it.
        "depth_m": {"median": round(float(np.median(depth[near_mask] if near_mask.any() else depth)), 3),
                    "p10": round(float(np.percentile(depth, 10)), 3), "p90": round(float(np.percentile(depth, 90)), 3)},
        "model": "sharp", "seconds": round(seconds, 2),
        **({"world": placed} if world and (placed := world(g)) else {}),
        "made": datetime.now().astimezone().isoformat(timespec="seconds"),
        **({"source": source} if source else {}),
    }  # fmt: skip
    (folder / "memory.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return meta


def memory_id(data: bytes) -> str:
    return "mem_" + hashlib.blake2b(data, digest_size=6).hexdigest()


def inbox_photos(vault_root: Path, settled_s: float = SETTLED_S) -> list[Path]:
    inbox = vault_root / "inbox" / "memories"
    if not inbox.is_dir():
        return []
    now = time.time()
    return sorted(p for p in inbox.iterdir()
                  if p.is_file() and p.suffix.lower() in SUFFIXES and now - p.stat().st_mtime >= settled_s)  # fmt: skip


def from_inbox(vault: object, predict: Callable, say: Callable[[str], None] = print) -> list[dict]:
    """Every photo in ``vault/inbox/memories``: a memory in the day it was taken, the photo moved in.
    One that cannot be read goes to ``failed/``, the reason beside it, so it is not tried again
    every time; a failure in SHARP itself (the GPU busy, say) stops the run, the photos left for the
    next."""
    inbox = vault.root / "inbox" / "memories"  # type: ignore[attr-defined]
    inbox.mkdir(parents=True, exist_ok=True)
    made = []
    for path in inbox_photos(vault.root):  # type: ignore[attr-defined]
        try:
            rgb, f_px, when = load_photo(path)
        except Exception as error:  # noqa: BLE001 - set aside with its reason; the others go on
            failed = inbox / "failed"
            failed.mkdir(exist_ok=True)
            shutil.move(str(path), failed / path.name)
            (failed / f"{path.name}.txt").write_text(f"{type(error).__name__}: {error}\n", encoding="utf-8")
            say(f"{path.name}: not read ({type(error).__name__}: {error}), moved to failed")
            continue
        ident = memory_id(path.read_bytes())
        date = when.strftime("%Y-%m-%d")
        folder = vault.day_path(date, must_exist=False) / "memories" / ident  # type: ignore[attr-defined]
        say(f"{path.name}: {rgb.shape[1]}x{rgb.shape[0]}, taken {when:%Y-%m-%d %H:%M}")
        meta = make(
            rgb, f_px, folder, predict=predict, source={"file": path.name, "taken": when.isoformat(timespec="seconds")}
        )
        shutil.move(str(path), folder / f"original{path.suffix.lower()}")
        made.append({"date": date, "id": ident} | meta)
    return made


def latest_scan(vault: object) -> str | None:
    """The newest day with a scan whose capture (video, poses and depth) is still there."""
    from ..splat.run import find_capture

    for scene in sorted(vault.root.glob("????-??-??/moments/mo_scan_*/scene.json"), reverse=True):  # type: ignore[attr-defined]
        moment = scene.parent
        if (moment / "capture" / "odometry.csv").is_file() or find_capture(vault, moment) is not None:  # type: ignore[arg-type]
            return moment.parent.parent.name
    return None


def have_memories(vault_root: Path) -> bool:
    return any(vault_root.glob("????-??-??/memories/mem_*/memory.json"))


class Lazy:
    """SHARP, loaded (2.8 GB, a few seconds) only when a photo is actually made."""

    def __init__(self, load: Callable[[], Callable]) -> None:
        self.load, self.predict = load, None

    def __call__(self, rgb: np.ndarray, f_px: float) -> dict:
        if self.predict is None:
            self.predict = self.load()
        return self.predict(rgb, f_px)


def lidar_scale(
    means_cv: np.ndarray, K: np.ndarray, width: int, lidar: np.ndarray, confidence: np.ndarray
) -> float | None:
    """The scan's metres per SHARP metre for one frame: the LiDAR's depth over the nearest Gaussian's
    at each reliable LiDAR pixel, the median (on the 2026-10-24 trail SHARP's depth was about half
    the LiDAR's)."""
    from . import single

    small = (lidar.shape[1], lidar.shape[0])
    seen = single.depth_of_points(means_cv, single.scale_K(np.asarray(K, float), small[0] / width), small)
    good = np.isfinite(seen) & (confidence >= 1) & (lidar > 0.2) & (lidar < single.LIDAR_RANGE_M)
    if good.sum() < 200:
        return None
    return round(float(np.median(lidar[good] / seen[good])), 4)


def from_scan(
    vault: object, date: str, count: int, predict: Callable, say: Callable[[str], None] = print
) -> list[dict]:
    """The sharpest frame in each of ``count`` stretches of the day's first scan with its capture, as memories placed
    in the scan: each with its camera pose in the scan's world and the LiDAR's scale."""
    from ..splat import dataset
    from ..splat.run import find_capture, scans
    from ..vault import read_json
    from . import simulate, single

    capture = moment = moment_id = None
    for moment_id in scans(vault, date):  # type: ignore[arg-type]
        moment = vault.day_path(date) / "moments" / moment_id  # type: ignore[attr-defined]
        capture = moment / "capture" if (moment / "capture" / "odometry.csv").is_file() else find_capture(vault, moment)  # type: ignore[arg-type]
        if capture is not None:
            break
    if capture is None or moment is None:
        return []
    scene_json = read_json(moment / "scene.json") or {}
    video = moment / scene_json["video"] if (moment / str(scene_json.get("video"))).is_file() else capture / "rgb.mp4"
    scene = single.read_scene(capture, video)
    sharp = dataset.sharpness(video, scene.width, scene.height)[: len(scene.t)]
    picks = dataset.choose(sharp, count, scene.t >= simulate.SETTLE_S)
    frames, found = dataset.frames_at(video, picks, (scene.width, scene.height))
    lidar = single.lidar_of(capture, scene, [int(i) for i in picks])
    meta_t0 = (read_json(moment / "meta.json") or {}).get("t0")
    made = []
    for k, i in enumerate(picks):
        if not found[k]:
            continue
        i = int(i)
        ident = memory_id(f"{moment_id}:{i}".encode())
        say(f"{moment_id} frame {i} ({scene.t[i]:.1f} s)")
        folder = vault.day_path(date) / "memories" / ident  # type: ignore[attr-defined]
        source = {"moment": moment_id, "frame": i, "t_s": round(float(scene.t[i]), 2), "t0": meta_t0}
        pose = scene.c2w[i] @ simulate.OPENGL  # OpenCV camera axes to OpenGL's, as the scan's splat and cameras

        def world(g: dict, i: int = i, pose: np.ndarray = pose) -> dict:
            scale = lidar_scale(g["means"], scene.K[i], scene.width, *lidar[i]) if i in lidar else None
            return {"moment": moment_id, "pose": [[round(float(v), 6) for v in row] for row in pose], "scale": scale or 1.0,
                    "scale_from": "lidar" if scale else "none"}  # fmt: skip

        meta = make(frames[k], float(scene.K[i][0]), folder, predict=predict, source=source, world=world)
        made.append({"date": date, "id": ident} | meta)
    return made


def main() -> None:
    from ..config import Settings
    from ..vault import Vault

    parser = argparse.ArgumentParser(description="Photos you can step into (SHARP)")
    parser.add_argument("photos", nargs="*", type=Path)
    parser.add_argument("--inbox", action="store_true", help="Every photo in vault/inbox/memories")
    parser.add_argument(
        "--scan", metavar="DATE", help="The sharpest frames of the day's first scan ('latest': the newest)"
    )
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--if-none", action="store_true", help="With --scan: only when the vault has no memories yet")
    args = parser.parse_args()
    vault = Vault(Settings().vault_root)
    say = lambda line: print(f"[{time.strftime('%H:%M:%S')}] {line}", flush=True)  # noqa: E731

    def load() -> Sharp:
        say("Loading SHARP")
        return Sharp()

    predict = Lazy(load)
    made = []
    for path in args.photos:
        rgb, f_px, when = load_photo(path)
        date = when.strftime("%Y-%m-%d")
        ident = memory_id(path.read_bytes())
        folder = vault.day_path(date, must_exist=False) / "memories" / ident
        meta = make(rgb, f_px, folder, predict=predict, source={"file": path.name})
        made.append({"date": date, "id": ident} | meta)
    if args.inbox:
        made += from_inbox(vault, predict, say)
    if args.scan and not (args.if_none and have_memories(vault.root)):
        date = latest_scan(vault) if args.scan == "latest" else args.scan
        if date is None:
            say("no scan with its capture in the vault")
        else:
            made += from_scan(vault, date, args.count, predict, say)
    for m in made:
        print(
            f"memory {m['date']}/{m['id']}: {m['splats']} splats, scene {m['depth_m']['median']} m away, {m['seconds']} s"
        )
    print(f"{len(made)} memories made")


if __name__ == "__main__":
    main()

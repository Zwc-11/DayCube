"""Camera poses from images alone: structure from motion with COLMAP (BSD) through pycolmap.

This is what a walk taken with glasses gives: photos or a video stream, no LiDAR, and no phone
tracking where the camera was. The images of one walk, in the order they were taken, go through:

- **Features.** SIFT on every image (at most ``max_image_size`` px), one shared camera (the
  glasses' or the phone's own), pinhole: Brush, the trainer, ignores lens distortion, and a phone's
  or the glasses' frames come already undistorted by their camera.
- **Matching.** Each image against the next ``overlap`` (and, quadratically, a few further on):
  a walk sees each place only from the images just before and after.
- **Mapping.** Incremental (COLMAP's default), or global (GLOMAP, in COLMAP since 4.0). The largest
  model is kept; the others are walks the matching could not join up. COLMAP's defaults for the
  first pair of images suit a camera moving around a scene: they refuse a pair whose camera moved
  mostly along its own view (``init_max_forward_motion`` 0.95), which is every pair of a walk
  looking ahead, and ask for 16 degrees between their rays. Here any motion is allowed, with
  ``INIT_MIN_TRI_ANGLE`` degrees: on 2026-10-26 the trail's photos a second apart came out in
  three pieces of 16 of 129 with the defaults, and a synthetic walk's photos 2 m apart in none.

pycolmap installs from pip on Windows and Linux (wheels for Python 3.10-3.14), CPU only: a few
hundred images of a walk take minutes.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

INIT_MIN_TRI_ANGLE = 4.0  # degrees between the first pair's rays (COLMAP: 16)


@dataclass
class Model:
    names: list[str]  # registered images, in the order of their file names
    c2w: np.ndarray  # (n, 4, 4) camera to world, OpenCV convention (x right, y down, z forward), model units
    K: np.ndarray  # (4,) fx, fy, cx, cy of the shared camera, at the images' size
    size: tuple[int, int]  # width, height
    points: np.ndarray  # (m, 3) the sparse points, model units
    colours: np.ndarray  # (m, 3) uint8
    images: int  # images given
    seconds: float
    mapper: str
    models: int  # models found (the walk in pieces when above 1)
    reprojection_px: float = 0.0
    timings: dict = field(default_factory=dict)


def available() -> bool:
    try:
        import pycolmap  # noqa: F401
    except ImportError:
        return False
    return True


def _cam_from_world(image: object) -> np.ndarray:
    pose = image.cam_from_world  # type: ignore[attr-defined]
    pose = pose() if callable(pose) else pose
    m = np.eye(4)
    m[:3, :4] = np.asarray(pose.matrix())
    return m


def reconstruct(
    images: Path,
    work: Path,
    *,
    overlap: int = 10,
    mapper: str = "incremental",
    max_image_size: int = 1600,
    max_features: int = 8192,
    features: str = "sift",
    say: Callable[[str], None] = print,
) -> Model | None:
    """Reconstruct the images in ``images`` (taken in file-name order) into ``work``. None when
    no model could be made. ``max_features``: SIFT features per image; matching costs their
    square (a 5 fps stream of 642 frames: 5.5 s a frame at 8,192 on the PC's CPU). ``features``:
    ``sift`` (COLMAP's), or ``aliked`` (ALIKED + LightGlue, ``learned``; needs PyTorch)."""
    import pycolmap

    names = sorted(p.name for p in images.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if len(names) < 3:
        return None
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    database = work / "database.db"
    timings: dict[str, float] = {}
    started = time.monotonic()

    if features == "aliked":
        from . import learned

        timings.update(learned.match(images, database, names, overlap=overlap, say=say))
    else:
        reader = pycolmap.ImageReaderOptions()
        reader.camera_model = "PINHOLE"
        extraction = pycolmap.FeatureExtractionOptions()
        extraction.max_image_size = max_image_size
        extraction.sift.max_num_features = max_features
        say(f"Features in {len(names)} images")
        pycolmap.extract_features(database, images, image_names=names, camera_mode=pycolmap.CameraMode.SINGLE,
                                  reader_options=reader, extraction_options=extraction)  # fmt: skip
        timings["features_s"] = round(time.monotonic() - started, 1)

        pairing = pycolmap.SequentialPairingOptions()
        pairing.overlap = overlap
        pairing.quadratic_overlap = True
        pairing.loop_detection = False  # needs a vocabulary tree; a walk rarely comes back on itself
        say("Matching each image with the ones after it")
        mark = time.monotonic()
        pycolmap.match_sequential(database, pairing_options=pairing)
        timings["matching_s"] = round(time.monotonic() - mark, 1)

    say(f"Mapping ({mapper})")
    mark = time.monotonic()
    out = work / "sparse"
    out.mkdir()
    if mapper == "global":
        models = pycolmap.global_mapping(database, images, out)
    else:
        options = pycolmap.IncrementalPipelineOptions()
        options.multiple_models = True
        options.mapper.init_max_forward_motion = 1.0  # a walk looks the way it goes
        options.mapper.init_min_tri_angle = INIT_MIN_TRI_ANGLE
        models = pycolmap.incremental_mapping(database, images, out, options)
    timings["mapping_s"] = round(time.monotonic() - mark, 1)
    if not models:
        return None
    best = max(models.values(), key=lambda r: r.num_reg_images())
    registered = sorted((im for im in best.images.values() if im.has_pose), key=lambda im: im.name)
    if len(registered) < 3:
        return None
    c2w = np.stack([np.linalg.inv(_cam_from_world(im)) for im in registered])
    camera = best.cameras[registered[0].camera_id]
    fx, fy, cx, cy = (float(v) for v in camera.params[:4])
    points = best.points3D.values()
    xyz = np.array([p.xyz for p in points]) if len(best.points3D) else np.zeros((0, 3))
    rgb = np.array([p.color for p in points], dtype=np.uint8) if len(best.points3D) else np.zeros((0, 3), np.uint8)
    best.write_text(str(out))  # cameras.txt, images.txt, points3D.txt: readable without pycolmap
    return Model(
        names=[im.name for im in registered], c2w=c2w, K=np.array([fx, fy, cx, cy]),
        size=(int(camera.width), int(camera.height)), points=xyz, colours=rgb, images=len(names),
        seconds=round(time.monotonic() - started, 1), mapper=f"{features}+{mapper}", models=len(models),
        reprojection_px=round(float(best.compute_mean_reprojection_error()), 3), timings=timings,
    )  # fmt: skip

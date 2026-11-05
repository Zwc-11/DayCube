"""Learned features for walks SIFT cannot join: ALIKED keypoints matched by LightGlue (Kornia,
Apache-2.0; ALIKED BSD-3, LightGlue Apache-2.0), written into a COLMAP database, which COLMAP
verifies and maps as it does its own.

On the 2026-10-24 trail, photos 3.8 m apart had about 17,000 SIFT features each, and most
neighbouring pairs no match at all: the trees look different enough 4 m on. Learned features are
made for that change of viewpoint. A synthetic walk's photos 2 m apart: 490-880 matches a pair.

PyTorch and Kornia are needed (``services/recon``'s environment; the GPU when there is one). The
weights are fetched once (Hugging Face, ``~/.cache/torch/hub``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

MAX_KEYPOINTS = 4096
MAX_SIDE = 1600  # images are read at most this many pixels on their long side


def available() -> bool:
    try:
        import kornia.feature  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def sequential_pairs(count: int, overlap: int) -> list[tuple[int, int]]:
    """Each image with the next ``overlap``, and quadratically further on (1, 2, 4, 8, ... after
    the overlap), as COLMAP's sequential matcher pairs them."""
    pairs = set()
    for i in range(count):
        for k in range(1, overlap + 1):
            if i + k < count:
                pairs.add((i, i + k))
        step = 1
        while i + overlap + step < count:
            pairs.add((i, i + overlap + step))
            step *= 2
    return sorted(pairs)


class Features:
    """ALIKED and LightGlue loaded once (the weights are fetched the first time) on ``device``: the
    GPU when there is one."""

    def __init__(self, max_keypoints: int = MAX_KEYPOINTS, device: object = None) -> None:
        import kornia.feature as KF
        import torch

        self.torch, self.KF = torch, KF
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.aliked = KF.ALIKED.from_pretrained("aliked-n16", max_num_keypoints=max_keypoints, device=self.device).eval()
        self.matcher = KF.LightGlueMatcher("aliked").eval().to(self.device)

    def detect(self, image: np.ndarray) -> tuple:
        """Keypoints and descriptors of an RGB image (read at most ``MAX_SIDE`` px on its long side):
        ``(keypoints at the size read, descriptors, (height, width) read, scale read / own)``."""
        from PIL import Image

        torch = self.torch
        height, width = image.shape[:2]
        scale = min(1.0, MAX_SIDE / max(height, width))
        if scale < 1.0:
            size = (round(width * scale), round(height * scale))
            image = np.asarray(Image.fromarray(image).resize(size, Image.Resampling.LANCZOS))
        tensor = torch.from_numpy(np.ascontiguousarray(image)).permute(2, 0, 1)[None].float().div(255).to(self.device)
        with torch.inference_mode():
            found = self.aliked(tensor)[0]
        return found.keypoints.float(), found.descriptors.float(), tuple(image.shape[:2]), scale

    @staticmethod
    def pixels(found: tuple) -> np.ndarray:
        """Keypoints in the image's own pixels (pixel centres at whole numbers)."""
        return found[0].cpu().numpy() / found[3]

    def match(self, a: tuple, b: tuple) -> np.ndarray:
        """(m, 2) indices of the keypoints of ``a`` and ``b`` that LightGlue pairs."""
        torch, KF = self.torch, self.KF
        ka, da, hwa, _ = a
        kb, db, hwb, _ = b
        with torch.inference_mode():
            lafs_a = KF.laf_from_center_scale_ori(ka[None], torch.ones(1, len(ka), 1, 1, device=self.device))
            lafs_b = KF.laf_from_center_scale_ori(kb[None], torch.ones(1, len(kb), 1, 1, device=self.device))
            _, index = self.matcher(da, db, lafs_a, lafs_b, hw1=hwa, hw2=hwb)
        return index.cpu().numpy().astype(np.uint32)


def match(images: Path, database: Path, names: list[str], *, overlap: int = 10, max_keypoints: int = MAX_KEYPOINTS,
          say: Callable[[str], None] = print) -> dict:  # fmt: skip
    """Import ``names`` (one pinhole camera), find ALIKED keypoints, match pairs with LightGlue and
    write keypoints and matches to ``database``. Returns timings and counts; COLMAP's
    ``verify_matches`` and mapping follow (``sfm.reconstruct``)."""
    import pycolmap
    from PIL import Image

    pycolmap.Database.open(str(database)).close()
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "PINHOLE"
    pycolmap.import_images(database, images, camera_mode=pycolmap.CameraMode.SINGLE, image_names=names, options=reader)
    started = time.monotonic()
    features = Features(max_keypoints)
    say(f"ALIKED keypoints in {len(names)} images ({features.device.type})")
    found = [features.detect(np.asarray(Image.open(images / name).convert("RGB"))) for name in names]
    extracted = time.monotonic()
    db = pycolmap.Database.open(str(database))
    ids = {image.name: image.image_id for image in db.read_all_images()}
    for name, item in zip(names, found, strict=True):
        # COLMAP's pixel centres are at .5; keypoints back to the image's own size.
        db.write_keypoints(ids[name], (Features.pixels(item) + 0.5).astype(np.float32))
    pairs = sequential_pairs(len(names), overlap)
    say(f"LightGlue on {len(pairs)} pairs")
    lines = []
    total = 0
    for i, j in pairs:
        index = features.match(found[i], found[j])
        if len(index):
            db.write_matches(ids[names[i]], ids[names[j]], index)
            lines.append(f"{names[i]} {names[j]}")
            total += len(index)
    db.close()
    pairs_file = database.parent / "pairs.txt"
    pairs_file.write_text("\n".join(lines) + "\n")
    say("Verifying the matches' geometry")
    pycolmap.verify_matches(database, pairs_file)
    return {
        "device": features.device.type, "pairs": len(pairs), "matched_pairs": len(lines), "matches": total,
        "features_s": round(extracted - started, 1), "matching_s": round(time.monotonic() - extracted, 1),
    }  # fmt: skip

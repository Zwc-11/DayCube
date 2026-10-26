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


def match(images: Path, database: Path, names: list[str], *, overlap: int = 10, max_keypoints: int = MAX_KEYPOINTS,
          say: Callable[[str], None] = print) -> dict:  # fmt: skip
    """Import ``names`` (one pinhole camera), find ALIKED keypoints, match pairs with LightGlue and
    write keypoints and matches to ``database``. Returns timings and counts; COLMAP's
    ``verify_matches`` and mapping follow (``sfm.reconstruct``)."""
    import kornia.feature as KF
    import pycolmap
    import torch
    from PIL import Image

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    pycolmap.Database.open(str(database)).close()
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "PINHOLE"
    pycolmap.import_images(database, images, camera_mode=pycolmap.CameraMode.SINGLE, image_names=names, options=reader)
    started = time.monotonic()
    aliked = KF.ALIKED.from_pretrained("aliked-n16", max_num_keypoints=max_keypoints, device=device).eval()
    matcher = KF.LightGlueMatcher("aliked").eval().to(device)
    say(f"ALIKED keypoints in {len(names)} images ({device.type})")
    features = []
    with torch.inference_mode():
        for name in names:
            image = Image.open(images / name).convert("RGB")
            scale = min(1.0, MAX_SIDE / max(image.size))
            if scale < 1.0:
                image = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
            tensor = torch.from_numpy(np.array(image)).permute(2, 0, 1)[None].float().div(255).to(device)
            found = aliked(tensor)[0]
            features.append((found.keypoints.float(), found.descriptors.float(), (image.height, image.width), scale))
    extracted = time.monotonic()
    db = pycolmap.Database.open(str(database))
    ids = {image.name: image.image_id for image in db.read_all_images()}
    for name, (keypoints, _, _, scale) in zip(names, features, strict=True):
        # COLMAP's pixel centres are at .5; keypoints back to the image's own size.
        db.write_keypoints(ids[name], ((keypoints.cpu().numpy() + 0.5) / scale).astype(np.float32))
    pairs = sequential_pairs(len(names), overlap)
    say(f"LightGlue on {len(pairs)} pairs")
    lines = []
    total = 0
    with torch.inference_mode():
        for i, j in pairs:
            ki, di, hwi, _ = features[i]
            kj, dj, hwj, _ = features[j]
            lafs_i = KF.laf_from_center_scale_ori(ki[None], torch.ones(1, len(ki), 1, 1, device=device))
            lafs_j = KF.laf_from_center_scale_ori(kj[None], torch.ones(1, len(kj), 1, 1, device=device))
            _, index = matcher(di, dj, lafs_i, lafs_j, hw1=hwi, hw2=hwj)
            found = index.cpu().numpy().astype(np.uint32)
            if len(found):
                db.write_matches(ids[names[i]], ids[names[j]], found)
                lines.append(f"{names[i]} {names[j]}")
                total += len(found)
    db.close()
    pairs_file = database.parent / "pairs.txt"
    pairs_file.write_text("\n".join(lines) + "\n")
    say("Verifying the matches' geometry")
    pycolmap.verify_matches(database, pairs_file)
    return {
        "device": device.type, "pairs": len(pairs), "matched_pairs": len(lines), "matches": total,
        "features_s": round(extracted - started, 1), "matching_s": round(time.monotonic() - extracted, 1),
    }  # fmt: skip

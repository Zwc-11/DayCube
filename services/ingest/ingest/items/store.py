"""Items on disk: ``vault/<date>/items/<id>/`` (or any folder).

``items.json``::

    {"id": "it_<12 hex>", "made": ISO time, "source": {"kind": "scan" | "video", "file", ...},
     "frame": "world" | "shot",            # world: one frame for the whole recording (a scan's ARKit
                                           # metres, y up); shot: each shot's own camera
     "models": {"detector", "embedder", "depth"},
     "frames": [{"index", "t", "caption", "text", "items": [item ids]}],     # the recording's index
     "items": [{"id": "i0001", "label", "labels": {name: count}, "position": [x, y, z],
                "size_m", "spread_m", "seen", "first_s", "last_s", "depth": "lidar" | "estimate" | "model",
                "shot": n | null, "crop": "crops/i0001.jpg",
                "observations": [{"frame", "t", "box": [x0, y0, x1, y1]}]}]}

``vectors.f16``: one unit embedding per item, in ``items`` order, little-endian float16, for text
search. ``crops/``: each item's best look, a JPEG at most 256 px on its long side.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
from datetime import date as Date
from datetime import datetime
from pathlib import Path

import numpy as np

from ..moments.media import executable
from ..vault import Vault
from .merge import Item

CROP_PX = 256
KEPT_OBSERVATIONS = 24
SET = re.compile(r"it_[0-9a-f]{12}")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
CROP = re.compile(r"i\d{4}\.jpg")


def folder(vault: Vault, date: str, iid: str) -> Path:
    """``vault/<date>/items/<id>``; ValueError unless both are well formed."""
    if not DAY.fullmatch(date) or not SET.fullmatch(iid):
        raise ValueError("Not an items set")
    Date.fromisoformat(date)
    return vault.safe_path(f"{date}/items/{iid}")


def list_sets(vault: Vault) -> list[dict]:
    """Every items set in the vault, newest day first: what a list needs without the items."""
    out = []
    for path in vault.root.glob("????-??-??/items/it_*/items.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"date": path.parents[2].name, "id": path.parent.name, "source": record.get("source"),
                    "frame": record.get("frame"), "items": len(record.get("items") or []),
                    "frames": len(record.get("frames") or []), "made": record.get("made")})  # fmt: skip
    out.sort(key=lambda r: (r["date"], str(r["made"])), reverse=True)
    return out


def find(vault: Vault, words: str, top: int = 20) -> list[dict]:
    """Items whose names hold every word asked for, over the whole vault: most seen first. (By
    name only: finding by what a thing looks like needs the embedder, ``search.search``.)"""
    wanted = [w for w in words.lower().split() if w]
    hits = []
    if not wanted:
        return hits
    for listed in list_sets(vault):
        path = vault.root / listed["date"] / "items" / listed["id"] / "items.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        for item in record.get("items") or []:
            names = " ".join(item.get("labels") or [item.get("label", "")])
            if all(w in names for w in wanted):
                hits.append({"date": listed["date"], "set": listed["id"], "frame": record.get("frame"), **item})
    hits.sort(key=lambda h: (-h.get("seen", 0), h["date"]))
    return hits[:top]


def new_id() -> str:
    return f"it_{secrets.token_hex(6)}"


def jpeg(image: np.ndarray, path: Path, longest: int = CROP_PX) -> None:
    """An RGB picture as a JPEG, by ffmpeg (nothing beyond numpy is needed to store one)."""
    h, w = image.shape[:2]
    k = min(1.0, longest / max(h, w))
    size = f"{max(2, round(w * k / 2) * 2)}:{max(2, round(h * k / 2) * 2)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [executable("ffmpeg"), "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-i", "pipe:0",
         "-vf", f"scale={size}:flags=area", "-frames:v", "1", "-q:v", "4", str(path)],
        input=np.ascontiguousarray(image).tobytes(), check=True, timeout=120,
    )  # fmt: skip


def write(folder: Path, items: list[Item], crops: list[np.ndarray], *, source: dict, frame: str, models: dict,
          frames: list[dict], shots: list[int | None] | None = None, when: datetime | None = None) -> dict:  # fmt: skip
    folder.mkdir(parents=True, exist_ok=True)
    out_items = []
    for k, item in enumerate(items):
        iid = f"i{k + 1:04d}"
        labels: dict[str, int] = {}
        for o in item.observations:
            labels[o.label] = labels.get(o.label, 0) + 1
        obs = sorted(item.observations, key=lambda o: o.t)
        step = max(1, len(obs) // KEPT_OBSERVATIONS)
        jpeg(crops[k], folder / "crops" / f"{iid}.jpg")
        out_items.append({
            "id": iid, "label": item.label, "labels": dict(sorted(labels.items(), key=lambda kv: -kv[1])),
            "position": [round(float(v), 3) for v in item.position], "size_m": round(item.size, 3),
            "spread_m": round(item.spread_m, 3), "seen": len(obs), "first_s": round(obs[0].t, 2), "last_s": round(obs[-1].t, 2),
            "depth": item.best().depth, "shot": None if shots is None else shots[k], "crop": f"crops/{iid}.jpg",
            "observations": [{"frame": o.frame, "t": round(o.t, 2), "box": [round(float(v), 1) for v in o.box]} for o in obs[::step]],
        })  # fmt: skip
    record = {"id": folder.name if folder.name.startswith("it_") else new_id(),
              "made": (when or datetime.now().astimezone()).isoformat(timespec="seconds"),
              "source": source, "frame": frame, "models": models, "frames": frames, "items": out_items}  # fmt: skip
    vectors = np.stack([item.vector for item in items]).astype("<f2") if items else np.zeros((0, 1), "<f2")
    (folder / "vectors.f16").write_bytes(vectors.tobytes())
    (folder / "items.json").write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def read(folder: Path) -> tuple[dict, np.ndarray]:
    record = json.loads((folder / "items.json").read_text(encoding="utf-8"))
    raw = np.frombuffer((folder / "vectors.f16").read_bytes(), "<f2")
    n = len(record["items"])
    return record, raw.reshape(n, -1).astype(np.float32) if n else np.zeros((0, 1), np.float32)

"""Scans into 3D with no command: what the capture receiver does with new scans (``capture.scans``).

Every few minutes ``step`` looks at the vault's scan moments:

- **A scan with its depth and no 3D yet** gets its LiDAR preview at once (CPU, a minute or two).
- **A scan with only the preview** is trained on the GPU, one at a time, when training is allowed
  and the PC is free: idle for ``IDLE_S``, or at night (``NIGHT``). Training is allowed once Brush
  is installed, which the first ``daycube splat`` does with your say-so, or with
  ``DAYCUBE_SPLAT_TRAIN=1``; it never fetches a trainer on its own.
- **A failed training** is not retried until you run ``daycube splat`` again: the reason is in
  the moment's ``scene.json`` and in doctor.

``vault/derived/splat/status.json`` says what is waiting, what runs and what was last done.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..vault import Vault, atomic_text, read_json
from . import brush
from .run import find_capture, splat_moment

IDLE_S = 10 * 60
NIGHT = (1, 7)  # local hours [from, to)


def scans(vault: Vault) -> list[tuple[str, str, dict]]:
    """(date, moment id, its scene's splat entry or {}) for every scan in the vault, newest first."""
    out = []
    for scene_path in vault.root.glob("*/moments/mo_scan_*/scene.json"):
        scene = read_json(scene_path) or {}
        if scene.get("kind") != "camera-path":
            continue
        out.append((scene_path.parent.parent.parent.name, scene_path.parent.name, scene.get("splat") or {}))
    return sorted(out, key=lambda s: (s[0], s[1]), reverse=True)


def training_allowed() -> bool:
    return brush.locate() is not None or os.environ.get("DAYCUBE_SPLAT_TRAIN") == "1"


def free(idle_s: float | None, now: datetime) -> bool:
    """Whether the GPU may be taken: the person away long enough, or the night."""
    return (idle_s is not None and idle_s >= IDLE_S) or NIGHT[0] <= now.hour < NIGHT[1]


def _status(vault: Vault, **values: object) -> None:
    path = vault.root / "derived" / "splat" / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    current = read_json(path) if path.is_file() else {}
    atomic_text(path, json.dumps({**current, **values, "updated": time.time()}) + "\n")


def _survey(vault: Vault) -> tuple[list, list, list]:
    """Scans with only the preview, scans that failed, scans with depth and no 3D yet."""
    found = scans(vault)
    waiting = [(d, m) for d, m, s in found if s.get("status") == "lidar"]
    failed = [(d, m) for d, m, s in found if s.get("status") == "failed"]
    unmade = [
        (d, m) for d, m, s in found if not s and find_capture(vault, vault.day_path(d) / "moments" / m) is not None
    ]
    allowed = training_allowed()
    _status(vault, waiting=len(waiting) + len(unmade), failed=[f"{d}/{m}" for d, m in failed], training_allowed=allowed)
    return waiting, failed, unmade


def step(
    vault: Vault, *, idle_s: float | None, now: datetime | None = None, say: Callable[[str], None] = print
) -> dict:
    """Do the next thing one scan needs; returns what was done (``{"did": None}`` for nothing)."""
    now = now or datetime.now()
    waiting, _, unmade = _survey(vault)
    allowed = training_allowed()
    if unmade:
        date, moment = unmade[0]
        say(f"3D preview for {date} {moment}")
        splat_moment(vault, date, moment, train=False, say=say)
        _status(vault, last={"did": "preview", "moment": f"{date}/{moment}", "at": time.time()})
        _survey(vault)
        return {"did": "preview", "moment": f"{date}/{moment}"}
    if waiting and allowed and free(idle_s, now):
        date, moment = waiting[0]
        say(f"Training the 3D scene of {date} {moment}")
        _status(vault, running={"moment": f"{date}/{moment}", "since": time.time()})
        try:
            splat = splat_moment(vault, date, moment, train=True, say=say)
        finally:
            _status(vault, running=None)
            _survey(vault)
        _status(vault, last={"did": "trained", "moment": f"{date}/{moment}", "at": time.time(),
                             "splats": splat.get("splats"), "psnr_db": (splat.get("training") or {}).get("psnr_db")})  # fmt: skip
        return {"did": "trained", "moment": f"{date}/{moment}"}
    return {"did": None, "waiting": len(waiting), "allowed": allowed}


def report(vault_root: Path) -> dict | None:
    """What doctor shows: the status file, if the receiver ever looked."""
    path = vault_root / "derived" / "splat" / "status.json"
    return read_json(path) if path.is_file() else None

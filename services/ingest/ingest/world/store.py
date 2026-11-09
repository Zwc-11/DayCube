"""A world on disk: ``vault/<date>/world/<id>/world.json`` and each island's splat beside it.

world.json::

    {"id": "wd_<12 hex>", "date", "title", "made": ISO time,
     "source": {"file", "t0_s", "t1_s"} | null,
     "origin": {"lat", "lon", "ground_m"},            # ground_m: the ground's WGS84 ellipsoid height
     "islands": [{"id": "is_01", "splat": "is_01.splat", "points",
                  "enu": 4 x 4 row-major,             # island metres -> the world's frame (x east, y up, z south)
                  "bounds": {"min": [x, y, z], "max": [x, y, z]},   # in the world's frame
                  "located_by": "gps" | "landmark" | "landmark+footprints" | "manual", "error_m": float | null,
                  "kind": "seen" | "imagined", "shots": [[t0_s, t1_s], ...],
                  "view": {"eye": [x, y, z], "look": [x, y, z]}}]}   # optional, world frame: where it was filmed from

An island's splat is the viewer's 32-byte records (as a memory's), in the island's own metres.
Nothing from Google is ever written here (docs/world.md).
"""

from __future__ import annotations

import math
import re
import secrets
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Any

from ..vault import Record, Vault, atomic_bytes, read_json

WORLD = re.compile(r"wd_[0-9a-f]{12}")
ISLAND = re.compile(r"is_\d{2,3}")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
LOCATED_BY = frozenset({"gps", "landmark", "landmark+footprints", "manual"})
KINDS = frozenset({"seen", "imagined"})
RECORD = 32  # bytes per splat


def new_id() -> str:
    return f"wd_{secrets.token_hex(6)}"


def folder(vault: Vault, date: str, wid: str) -> Path:
    if not DAY.fullmatch(date) or not WORLD.fullmatch(wid):
        raise ValueError("Not a world")
    Date.fromisoformat(date)
    return vault.safe_path(f"{date}/world/{wid}")


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _check(ok: bool, what: str) -> None:
    if not ok:
        raise ValueError(f"Invalid world: {what}")


def validate(world: Record, date: str) -> None:
    """Raise ValueError unless ``world`` has the shape above and belongs to ``date``."""
    _check(WORLD.fullmatch(str(world.get("id", ""))) is not None, "id")
    _check(world.get("date") == date, "date")
    _check(isinstance(world.get("title"), str), "title")
    _check(isinstance(world.get("made"), str), "made")
    datetime.fromisoformat(world["made"])
    source = world.get("source")
    _check(source is None or (isinstance(source, dict) and isinstance(source.get("file"), str)
                              and _number(source.get("t0_s")) and _number(source.get("t1_s"))), "source")  # fmt: skip
    origin = world.get("origin")
    _check(isinstance(origin, dict) and all(_number(origin.get(k)) for k in ("lat", "lon", "ground_m")), "origin")
    _check(-90 <= origin["lat"] <= 90 and -180 <= origin["lon"] <= 180, "origin")
    islands = world.get("islands")
    _check(isinstance(islands, list), "islands")
    seen: set[str] = set()
    for island in islands:
        _check(isinstance(island, dict) and ISLAND.fullmatch(str(island.get("id", ""))) is not None, "island id")
        _check(island["id"] not in seen and island.get("splat") == f"{island['id']}.splat", f"{island['id']} splat")
        seen.add(island["id"])
        _check(isinstance(island.get("points"), int) and island["points"] >= 0, f"{island['id']} points")
        enu = island.get("enu")
        _check(isinstance(enu, list) and len(enu) == 4 and all(isinstance(r, list) and len(r) == 4 and all(map(_number, r))
                                                               for r in enu) and enu[3] == [0, 0, 0, 1], f"{island['id']} enu")  # fmt: skip
        bounds = island.get("bounds")
        _check(isinstance(bounds, dict) and all(isinstance(bounds.get(k), list) and len(bounds[k]) == 3
                                                and all(map(_number, bounds[k])) for k in ("min", "max"))
               and all(a <= b for a, b in zip(bounds["min"], bounds["max"], strict=True)), f"{island['id']} bounds")  # fmt: skip
        _check(island.get("located_by") in LOCATED_BY, f"{island['id']} located_by")
        error = island.get("error_m")
        _check(error is None or (_number(error) and error >= 0), f"{island['id']} error_m")
        _check(island.get("kind") in KINDS, f"{island['id']} kind")
        view = island.get("view")  # optional: where it was filmed from, and a point it looked at
        _check(view is None or (isinstance(view, dict) and set(view) == {"eye", "look"} and all(
            isinstance(view[k], list) and len(view[k]) == 3 and all(map(_number, view[k])) for k in view)),
               f"{island['id']} view")  # fmt: skip
        shots = island.get("shots")
        _check(isinstance(shots, list) and all(isinstance(s, list) and len(s) == 2 and all(map(_number, s)) and s[0] <= s[1]
                                               for s in shots), f"{island['id']} shots")  # fmt: skip


def save(vault: Vault, date: str, world: Record, splats: dict[str, bytes]) -> Path:
    """Write ``world`` and its islands' splats (keyed by file name); returns the world's folder.
    The splats are written first, so a world.json never names a file that is not there."""
    validate(world, date)
    names = {island["splat"] for island in world["islands"]}
    if set(splats) != names:
        raise ValueError("Invalid world: the splats given are not its islands'")
    for island in world["islands"]:
        data = splats[island["splat"]]
        if len(data) % RECORD or len(data) // RECORD != island["points"]:
            raise ValueError(f"Invalid world: {island['splat']} is not {island['points']} splat records")
    path = folder(vault, date, world["id"])
    with vault.lock:
        for name, data in splats.items():
            atomic_bytes(path / name, data)
        vault.write_json(path / "world.json", world)
    return path


def load(vault: Vault, date: str, wid: str) -> Record | None:
    path = folder(vault, date, wid) / "world.json"
    return read_json(path) if path.is_file() else None


def list_worlds(vault: Vault) -> list[Record]:
    """Newest first (day, then when made): what a list needs without opening each world."""
    out = []
    for path in vault.root.glob("????-??-??/world/wd_*/world.json"):
        try:
            world = read_json(path)
        except (OSError, ValueError):
            continue
        out.append({"date": path.parents[2].name, "id": path.parent.name, "title": world.get("title"),
                    "islands": len(world.get("islands") or []), "origin": world.get("origin"),
                    "made": world.get("made")})  # fmt: skip
    out.sort(key=lambda w: (w["date"], str(w["made"])), reverse=True)
    return [{k: v for k, v in w.items() if k != "made"} for w in out]

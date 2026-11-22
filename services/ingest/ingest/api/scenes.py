"""3D kept in the vault: memories (photos you can step into), worlds made from videos, the items
found in scans and videos, and the open map around them."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field

from ..integrations.overpass import OverpassAdapter
from ..items import store as item_sets
from ..vault import Record, Vault, atomic_bytes, read_json
from ..world import osm
from ..world import store as worlds
from .deps import AUTHORIZED, RequestModel, TheSettings, TheVault, state

router = APIRouter()

MEMORY_FILES = {"photo.jpg": "image/jpeg", "memory.splat": "application/octet-stream"}
FILMS = ("film-portrait.mp4", "film-landscape.mp4")  # a memory's film, as the viewer exports it
FILM_MAX = 512 * 1024 * 1024
NOSNIFF = {"X-Content-Type-Options": "nosniff"}


class MemoryEdit(RequestModel):
    """A memory's caption: the line shown under its photo at the end of its film; empty removes it."""

    caption: str = Field(max_length=280)


# Memories (ingest/recon/memory.py), in their day's ``memories`` folder.
@router.get("/memories")
def memories(vault: TheVault) -> list[Record]:
    """Newest first: each memory's day, id and what the viewer needs to show it."""
    out = []
    for meta_path in vault.root.glob("????-??-??/memories/mem_*/memory.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        keep = ("width", "height", "fx", "fov_y_deg", "splats", "depth_m", "model", "made", "source", "caption")
        date = meta_path.parents[2].name
        item = {"date": date, "id": meta_path.parent.name, **{k: meta.get(k) for k in keep}}
        # A photo from a scan stands in that scan's world: its splat (trained, else the LiDAR's)
        # and where the photo was taken in it.
        world = meta.get("world")
        if isinstance(world, dict) and re.fullmatch(r"[\w.-]+", str(world.get("moment", ""))):
            scene = read_json(vault.root / date / "moments" / world["moment"] / "scene.json") or {}
            splat = scene.get("splat") if isinstance(scene.get("splat"), dict) else {}
            name = splat.get("trained") or splat.get("lidar")
            if name:
                item["world"] = {**world, "splat": f"moments/{world['moment']}/{name}", "trained": bool(splat.get("trained")),
                                 "bounds": splat.get("bounds")}  # fmt: skip
        out.append(item)
    return sorted(out, key=lambda m: (m["date"], str(m.get("made"))), reverse=True)


def memory_path(vault: Vault, date: str, ident: str, name: str = "memory.json") -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not re.fullmatch(r"mem_[0-9a-f]{12}", ident):
        raise HTTPException(404, "Memory not found")
    path = vault.safe_path(f"{date}/memories/{ident}/{name}")
    if not (path.parent / "memory.json").is_file():
        raise HTTPException(404, "Memory not found")
    return path


@router.get("/memories/{date}/{ident}/{name}")
def memory_file(date: str, ident: str, name: str, vault: TheVault) -> FileResponse:
    if name not in MEMORY_FILES and name not in FILMS:
        raise HTTPException(404, "Memory not found")
    path = memory_path(vault, date, ident, name)
    if not path.is_file():
        raise HTTPException(404, "Memory not found")
    return FileResponse(path, media_type=MEMORY_FILES.get(name, "video/mp4"), headers=NOSNIFF)


@router.patch("/memories/{date}/{ident}", dependencies=AUTHORIZED)
def edit_memory(date: str, ident: str, body: MemoryEdit, vault: TheVault) -> Record:
    path = memory_path(vault, date, ident)
    with vault.lock:
        meta = {k: v for k, v in read_json(path).items() if k != "caption"}
        caption = " ".join(body.caption.split())
        vault.write_json(path, {**meta, "caption": caption} if caption else meta)
    return {"caption": caption or None}


@router.put("/memories/{date}/{ident}/{name}", dependencies=AUTHORIZED)
async def save_film(date: str, ident: str, name: str, request: Request, vault: TheVault) -> Record:
    """A film the viewer exported (MP4), kept beside its memory; the newest of each shape wins."""
    if name not in FILMS:
        raise HTTPException(404, "Memory not found")
    path = memory_path(vault, date, ident, name)
    data = await request.body()
    if len(data) > FILM_MAX or data[4:8] != b"ftyp":
        raise HTTPException(422, "Not an MP4 film")
    await asyncio.to_thread(atomic_bytes, path, data)
    return {"film": name, "bytes": len(data)}


# Worlds made from videos (ingest/world, docs/world.md): islands of a video's own splats, and
# the open map they stand in. Google's tiles are streamed by the viewer and never touch here.
@router.get("/worlds")
def world_list(vault: TheVault) -> list[Record]:
    return worlds.list_worlds(vault)


def world_of(vault: Vault, date: str, wid: str) -> tuple[Path, Record]:
    try:
        path = worlds.folder(vault, date, wid)
    except ValueError:
        raise HTTPException(404, "World not found") from None
    if not (path / "world.json").is_file():
        raise HTTPException(404, "World not found")
    return path, read_json(path / "world.json")


@router.get("/worlds/{date}/{wid}")
def world(date: str, wid: str, vault: TheVault) -> Record:
    return world_of(vault, date, wid)[1]


@router.get("/worlds/{date}/{wid}/{name}")
def world_file(date: str, wid: str, name: str, vault: TheVault) -> FileResponse:
    path, found = world_of(vault, date, wid)
    if name not in {island.get("splat") for island in found.get("islands") or []} or not (path / name).is_file():
        raise HTTPException(404, "World not found")
    return FileResponse(path / name, media_type="application/octet-stream", headers=NOSNIFF)


# Items (ingest/items, docs/items.md): the things found in a scan or a video, each placed in 3D.
@router.get("/items")
def items_list(vault: TheVault) -> list[Record]:
    """Every items set, newest day first."""
    return item_sets.list_sets(vault)


@router.get("/items/find")
def items_find(
    vault: TheVault, q: str = Query(min_length=1, max_length=200), top: int = Query(20, ge=1, le=100)
) -> list[Record]:
    """Items by name, then by the caption and writing of a frame that shows them, over the whole
    vault: where and when each was seen."""
    return item_sets.find(vault, q, top)


def items_of(vault: Vault, date: str, iid: str) -> Path:
    try:
        path = item_sets.folder(vault, date, iid)
    except ValueError:
        raise HTTPException(404, "Items not found") from None
    if not (path / "items.json").is_file():
        raise HTTPException(404, "Items not found")
    return path


@router.get("/items/{date}/{iid}")
def items_set(date: str, iid: str, vault: TheVault) -> Record:
    return read_json(items_of(vault, date, iid) / "items.json")


@router.get("/items/{date}/{iid}/crops/{name}")
def items_crop(date: str, iid: str, name: str, vault: TheVault) -> FileResponse:
    path = items_of(vault, date, iid) / "crops" / name
    if not item_sets.CROP.fullmatch(name) or not path.is_file():
        raise HTTPException(404, "Items not found")
    return FileResponse(path, media_type="image/jpeg", headers=NOSNIFF)


@router.get("/maps/buildings")
def buildings(
    overpass: Annotated[OverpassAdapter, state("overpass")],
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    radius_m: int = Query(300, ge=50, le=600),
) -> Record:
    """OpenStreetMap footprints with heights around a point (ODbL), fetched once and kept in
    vault/maps/osm; the rounded location is sent to Overpass the first time."""
    try:
        return osm.footprints(overpass.buildings(lat, lon, radius_m))
    except (httpx.HTTPError, ValueError) as error:
        why = f"OpenStreetMap is not reachable and this area is not kept yet ({type(error).__name__})"
        raise HTTPException(503, why) from None


@router.get("/maps/config", dependencies=AUTHORIZED)
def maps_config(settings: TheSettings) -> Record:
    """The Google Maps key for the viewer to stream Photorealistic 3D Tiles with; null without one."""
    return {"google_tiles_key": settings.google_maps_key.get_secret_value() or None}

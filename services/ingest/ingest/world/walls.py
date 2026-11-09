"""Which wall a shot was filmed beside, from a landmark's name (docs/world.md, "Finding the wall").

A one-view island knows its wall from the inside: how far the camera was from it, and how far
ahead the facade ran. The landmark's footprint gives the walls it could be. A wall is kept when
the seen facade fits on it, it faces a named street, and the sidewalk beside it is open (no other
building where the camera walked). One left: that wall. Several: they are listed for the user to
pick. Geometry alone never tells a symmetric building's sides apart (Union Station's two wings on
Front St), so nothing is guessed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..vault import Record
from .geo import lat_lon_from_local, local_from_lat_lon

TURN = math.radians(10)  # consecutive edges within this of straight are one wall...
STRAY_M = 1.0  # ...when no corner between them strays further than this from it
PARALLEL = math.cos(math.radians(25))
STREET_M = 40.0  # the street a wall faces is within this of it
SLACK_M = 2.0  # the seen facade may run this far past a wall's corners (outline and depth error)
STEP_M = 1.0  # where along a wall the shot may start, tried every metre
SHORTEST_M = 8.0  # shorter walls are corners and jogs, not what a walk is filmed beside


@dataclass(frozen=True)
class Seen:
    """What the island measured: the camera's distance from the facade, and the stretch of facade
    seen, ``start`` to ``end`` metres ahead of the camera."""

    distance: float
    start: float
    end: float


ANY = Seen(1.0, 0.0, 0.0)  # before the shot is measured: every wall with an open sidewalk

@dataclass(frozen=True)
class Wall:
    a: tuple[float, float]  # lat, lon: where the walk came from, the building on its right
    b: tuple[float, float]
    length: float
    street: str | None  # the named street it faces
    along: tuple[float, float]  # where on it the camera can have been, metres from a


def outer_rings(geojson: Record | None) -> list[np.ndarray]:
    """A GeoJSON Polygon's or MultiPolygon's outer rings, as (lon, lat) arrays."""
    kind = (geojson or {}).get("type")
    polygons = [geojson["coordinates"]] if kind == "Polygon" else geojson["coordinates"] if kind == "MultiPolygon" else []
    return [np.asarray(polygon[0], float) for polygon in polygons if polygon and len(polygon[0]) >= 4]


def inside(points: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """Which ``points`` (n, 2) are in the closed ``ring`` (m, 2), by even-odd crossings."""
    x, z = points[:, :1], points[:, 1:]
    (x0, z0), (x1, z1) = ring[:-1].T, ring[1:].T
    rise = np.where(z1 == z0, 1e-12, z1 - z0)
    return (((z0 > z) != (z1 > z)) & (x < x0 + (z - z0) * (x1 - x0) / rise)).sum(1) % 2 == 1


def straight_runs(ring: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """A closed ring's walls, corner to corner: its edges, with nearly straight runs joined."""
    pts = ring[:-1]
    n = len(pts)
    edge = np.roll(pts, -1, 0) - pts
    heading = np.arctan2(edge[:, 1], edge[:, 0])
    turn = np.abs((heading - np.roll(heading, 1) + math.pi) % (2 * math.pi) - math.pi)
    pts = np.roll(pts, -int(turn.argmax()), 0)  # from the sharpest corner: no run split by the start
    pts = np.vstack([pts, pts[:1]])
    runs, i = [], 0
    while i < n:
        j = i + 1
        while j < n:
            chord = pts[j + 1] - pts[i]
            length = float(np.hypot(*chord))
            nxt = pts[j + 1] - pts[j]
            cos = float(nxt @ chord) / max(float(np.hypot(*nxt)) * length, 1e-9)
            rel = pts[i + 1 : j + 1] - pts[i]
            stray = np.abs(chord[0] * rel[:, 1] - chord[1] * rel[:, 0]) / max(length, 1e-9)
            if cos < math.cos(TURN) or stray.max() > STRAY_M:
                break
            j += 1
        runs.append((pts[i], pts[j]))
        i = j
    return runs


def walls(landmark: list[np.ndarray], others: list[np.ndarray], streets: list[tuple[str, np.ndarray]],
          seen: Seen, origin: tuple[float, float]) -> list[Wall]:  # fmt: skip
    """The landmark's walls the shot fits beside (rings and street lines as (lon, lat) arrays;
    ``origin`` a nearby lat, lon), those facing a street first."""
    kx, kz = local_from_lat_lon(*origin, origin[0] + 1, origin[1] + 1)  # the frame is linear in lat, lon

    def local(lon_lat: np.ndarray) -> np.ndarray:
        return np.stack([(lon_lat[:, 0] - origin[1]) * kx, (lon_lat[:, 1] - origin[0]) * kz], 1)

    own = [local(r) for r in landmark]
    blocks = own + [local(r) for r in others]
    roads = [(name, local(line)) for name, line in streets if len(line) >= 2]
    found = []
    for ring in own:
        for p, q in straight_runs(ring):
            length = float(np.hypot(*(q - p)))
            if length < SHORTEST_M:
                continue
            t = (q - p) / length
            if not inside((p + q)[None] / 2 + 0.5 * np.array([[-t[1], t[0]]]), ring)[0]:
                p, q, t = q, p, -t  # walk it the way that has the building on the right
            out = np.array([t[1], -t[0]])  # the walk's left, away from the building
            starts = np.arange(-SLACK_M - seen.start, length + SLACK_M - seen.end + 1e-9, STEP_M)
            if not len(starts):
                continue  # the facade seen is longer than the wall
            ahead = np.arange(0.0, seen.end + 1e-9, 2.0)
            offsets = np.array([0.5, seen.distance, seen.distance + 3.0])
            # The sidewalk the camera walked: from it to the end of the seen facade, wall to street.
            spots = (p + (starts[:, None, None] + ahead[None, :, None])[..., None] * t
                     + offsets[None, None, :, None] * out).reshape(-1, 2)  # fmt: skip
            blocked = np.zeros(len(spots), bool)
            for block in blocks:
                lo, hi = block.min(0), block.max(0)
                near = np.all((spots >= lo) & (spots <= hi), 1)
                if near.any():
                    blocked[near] |= inside(spots[near], block)
            free = starts[~blocked.reshape(len(starts), -1).any(1)]
            if not len(free):
                continue
            faced = sorted((off, name) for name, line in roads if (off := _facing(line, p, t, out, length, seen.distance)) is not None)
            a, b = lat_lon_from_local(*origin, *p), lat_lon_from_local(*origin, *q)
            found.append(Wall(a, b, round(length, 1), faced[0][1] if faced else None, (float(free[0]), float(free[-1]))))
    return sorted(found, key=lambda w: (w.street is None, -w.length))


def _facing(line: np.ndarray, p: np.ndarray, t: np.ndarray, out: np.ndarray, length: float, distance: float) -> float | None:
    """How far out from the wall (``p`` along ``t``, ``length`` long) the street ``line`` runs
    beside it, parallel and beyond the camera; None where it does not."""
    nearest = None
    for a, b in zip(line[:-1], line[1:], strict=True):
        seg = b - a
        s0, s1 = float((a - p) @ t), float((b - p) @ t)
        if abs(s1 - s0) < PARALLEL * float(np.hypot(*seg)):
            continue  # not parallel (or a point)
        lo, hi = max(min(s0, s1), -5.0), min(max(s0, s1), length + 5.0)
        if lo > hi:
            continue  # not beside the wall
        off = float((a + seg * ((lo + hi) / 2 - s0) / (s1 - s0) - p) @ out)
        if distance <= off <= STREET_M and (nearest is None or off < nearest):
            nearest = off
    return nearest


def reach(place: Record) -> int:
    """The radius to ask for buildings and streets around ``place``: its outline and 60 m more."""
    far = [float(np.hypot(*local_from_lat_lon(place["lat"], place["lon"], lat, lon)))
           for ring in outer_rings(place.get("geojson")) for lon, lat in ring]  # fmt: skip
    return int(min(600, max(far, default=90) + 60))


def landmark_walls(place: Record, buildings: Record, streets: Record, seen: Seen) -> list[Wall]:
    """:func:`walls` for a Nominatim ``place``, with the open buildings (``osm.footprints``) and
    the Overpass streets around it. The landmark's outline is its own footprint among the
    buildings, else Nominatim's, else the building the place's point is in."""
    point = np.array([[place["lon"], place["lat"]]])
    shapes = [(f["properties"]["id"], ring) for f in buildings.get("features") or [] for ring in outer_rings(f["geometry"])]
    own = {ident for ident, _ in shapes if ident == place.get("osm")}
    landmark = [ring for ident, ring in shapes if ident in own] or outer_rings(place.get("geojson"))
    if not landmark:
        own = {ident for ident, ring in shapes if inside(point, ring)[0]}
        landmark = [ring for ident, ring in shapes if ident in own]
    if not landmark:
        raise LookupError(f"No building outline for {place.get('name')!r}: name the wall with --wall")
    others = [ring for ident, ring in shapes if ident not in own]
    lines = [(e["tags"]["name"], np.array([[g["lon"], g["lat"]] for g in e.get("geometry") or []], float))
             for e in streets.get("elements") or [] if (e.get("tags") or {}).get("name")]  # fmt: skip
    return walls(landmark, others, lines, seen, (place["lat"], place["lon"]))


def compass(east: float, north: float) -> str:
    """The 8-point compass direction of a vector."""
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[round(math.degrees(math.atan2(east, north)) / 45) % 8]


def label(wall: Wall, middle: tuple[float, float]) -> str:
    """Where a wall is on its building (seen from ``middle``, a lat, lon) and the way it is walked."""
    ax, az = local_from_lat_lon(*middle, *wall.a)
    bx, bz = local_from_lat_lon(*middle, *wall.b)
    return f"on the {compass((ax + bx) / 2, -(az + bz) / 2)} side, walking {compass(bx - ax, az - bz)}"

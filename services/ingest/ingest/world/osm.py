"""OpenStreetMap buildings around a point, as footprints with heights (ODbL): the open layer a
world stands in, kept in the vault, and what its islands are fitted to (docs/world.md)."""

from __future__ import annotations

import re

from ..vault import Record

ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"
LEVEL_M = 3.2  # metres per storey when only building:levels is given
DEFAULT_M = 10.0  # a building with neither
_FEET = 0.3048


def metres(value: object) -> float | None:
    """An OSM length: "153.57", "12 m", "40 ft", "40'"; None for anything else."""
    text = str(value or "").strip().lower()
    match = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*(m|ft|')?", text)
    if not match:
        return None
    number = float(match.group(1))
    return number * _FEET if match.group(2) in {"ft", "'"} else number


def heights(tags: Record) -> tuple[float, float, str]:
    """(top, bottom, where the top came from): the height tag, else storeys x 3.2 m, else 10 m."""
    top = metres(tags.get("height"))
    levels = metres(tags.get("building:levels"))
    bottom = metres(tags.get("min_height"))
    if bottom is None:
        min_level = metres(tags.get("building:min_level"))
        bottom = min_level * LEVEL_M if min_level is not None else 0.0
    if top is not None:
        return top, bottom, "height"
    if levels is not None:
        return levels * LEVEL_M, bottom, "levels"
    return DEFAULT_M, bottom, "default"


def _ring(geometry: object) -> list[list[float]] | None:
    points = [[p["lon"], p["lat"]] for p in geometry or [] if isinstance(p, dict) and "lat" in p and "lon" in p]
    if len(points) < 3:
        return None
    if points[0] != points[-1]:
        points.append(points[0])
    return points if len(points) >= 4 else None


def footprints(overpass: Record) -> Record:
    """An Overpass ``out geom tags`` answer as a GeoJSON FeatureCollection: one Polygon per way,
    and per closed outer way of a multipolygon relation (an outer split over several ways is left
    out rather than stitched wrong)."""
    features = []
    for element in overpass.get("elements") or []:
        tags = element.get("tags") or {}
        if element.get("type") == "way":
            rings = [_ring(element.get("geometry"))]
        elif element.get("type") == "relation":
            rings = [_ring(m.get("geometry")) for m in element.get("members") or [] if m.get("role") == "outer"]
        else:
            continue
        top, bottom, source = heights(tags)
        ident = f"{element['type']}/{element['id']}"
        for ring in rings:
            if ring is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"id": ident, "name": tags.get("name"), "height_m": round(top, 2),
                               "min_height_m": round(bottom, 2), "height_from": source},
            })  # fmt: skip
    return {"type": "FeatureCollection", "attribution": ATTRIBUTION, "features": features}

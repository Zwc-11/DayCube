"""Distances and local coordinates on the Earth (spherical; metres)."""

from __future__ import annotations

import math

EARTH_M = 6_371_008.8


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(a)))


def local(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Metres east and north of (lat0, lon0) (equirectangular: exact enough within a few km)."""
    x = math.radians(lon - lon0) * EARTH_M * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * EARTH_M
    return x, y

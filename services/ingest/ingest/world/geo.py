"""Where a world stands on the Earth: a local frame in metres around an origin, and the ground's
height there on the WGS84 ellipsoid (the height 3D Tiles use).

The local frame has three.js axes: x east, y up, z south. It is a tangent plane with the WGS84
radii of curvature at the origin, so within 2 km of it a metre is a metre to well under 0.1%.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ..vault import atomic_bytes

if TYPE_CHECKING:
    from PIL.Image import Image

WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3  # first eccentricity squared
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"  # Copernicus DEM GLO-90, CC BY 4.0
# Canada's CDEM: the bare ground (OGL - Canada). GLO-90 is a surface model, so downtown it can read
# rooftops: at Union Station, Toronto, 90 m against the street's ~80; CDEM gives 81 at Front St.
CANADA_URL = "https://geogratis.gc.ca/services/elevation/cdem/altitude"
CANADA = (41.6, 83.2, -141.1, -52.6)  # south, north, west, east
GEOID_URL = "https://cdn.proj.org/us_nga_egm96_15.tif"  # EGM96 on a 15' grid, public domain (NGA)
GEOID_FILE = "egm96_15.tif"
AGENT = {"User-Agent": "DAYCUBE/0.1 local-development"}


def _radii(lat: float) -> tuple[float, float]:
    """Metres per radian north (meridian) and east (prime vertical, before the cos(lat))."""
    s = math.sin(math.radians(lat))
    w = 1 - WGS84_E2 * s * s
    return WGS84_A * (1 - WGS84_E2) / w**1.5, WGS84_A / math.sqrt(w)


def local_from_lat_lon(origin_lat: float, origin_lon: float, lat: float, lon: float) -> tuple[float, float]:
    """(x east, z south) metres of ``lat, lon`` from the origin."""
    north, east = _radii(origin_lat)
    x = math.radians(lon - origin_lon) * east * math.cos(math.radians(origin_lat))
    return x, -math.radians(lat - origin_lat) * north


def lat_lon_from_local(origin_lat: float, origin_lon: float, x: float, z: float) -> tuple[float, float]:
    """The inverse of :func:`local_from_lat_lon`."""
    north, east = _radii(origin_lat)
    lat = origin_lat + math.degrees(-z / north)
    return lat, origin_lon + math.degrees(x / (east * math.cos(math.radians(origin_lat))))


@lru_cache(maxsize=2)
def _grid(path: Path) -> Image:
    from PIL import Image  # the moments extra: only the island pipeline asks for heights

    image = Image.open(path)
    image.load()
    return image


def undulation(grid: Path, lat: float, lon: float) -> float:
    """The geoid's height above the ellipsoid, metres, bilinear between the grid's points. The
    grid is global and its samples are points (GeoTIFF PixelIsPoint): column 0 at 180°W, row 0 at
    90°N, rows running to 90°S inclusive, columns wrapping round."""
    image = _grid(grid)
    width, height = image.size
    step = 360 / width
    if abs((height - 1) * step - 180) > 1e-9:
        raise ValueError("Not a global point grid")
    row = min(max((90 - lat) / step, 0.0), height - 1.0)
    col = ((lon + 180) % 360) / step
    r0, c0 = min(int(row), height - 2), int(col) % width
    fr, fc = row - r0, col - int(col)
    c1 = (c0 + 1) % width
    top = image.getpixel((c0, r0)) * (1 - fc) + image.getpixel((c1, r0)) * fc
    bottom = image.getpixel((c0, r0 + 1)) * (1 - fc) + image.getpixel((c1, r0 + 1)) * fc
    return float(top * (1 - fr) + bottom * fr)


def ground_height(lat: float, lon: float, cache: Path) -> float:
    """The ground's ellipsoid height at ``lat, lon``: its elevation above the geoid (``elevation``)
    plus the geoid's undulation. Sends the location to the elevation service; the geoid grid
    (2.7 MB) is fetched once into ``cache``."""
    grid = cache / GEOID_FILE
    if not grid.is_file():
        response = httpx.get(GEOID_URL, timeout=60, headers=AGENT, follow_redirects=True)
        response.raise_for_status()
        atomic_bytes(grid, response.content)
    return elevation(lat, lon) + undulation(grid, lat, lon)


def elevation(lat: float, lon: float) -> float:
    """The ground's height above the geoid: Canada's bare-ground CDEM inside Canada, where it
    answers, else Copernicus GLO-90 through Open-Meteo (a surface model: high in a city's core)."""
    south, north, west, east = CANADA
    if south <= lat <= north and west <= lon <= east:
        response = httpx.get(CANADA_URL, params={"lat": f"{lat:.5f}", "lon": f"{lon:.5f}"}, timeout=20, headers=AGENT)
        altitude = response.json().get("altitude") if response.status_code == 200 else None
        if isinstance(altitude, (int, float)):
            return float(altitude)
    response = httpx.get(ELEVATION_URL, params={"latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}"}, timeout=20, headers=AGENT)
    response.raise_for_status()
    return float(response.json()["elevation"][0])

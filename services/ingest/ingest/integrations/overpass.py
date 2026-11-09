import hashlib
import math
from pathlib import Path

import httpx

from ..vault import atomic_text, read_json

AGENT = {"User-Agent": "DAYCUBE/0.1 local-development"}  # Overpass answers 406 without one
# Streets a walk can be beside: named ways for traffic or for people on foot (a named sidewalk is
# mapped as part of its street), not paths, steps or corridors.
STREETS = "^(primary|secondary|tertiary|residential|unclassified|living_street|pedestrian|service)$"


class OverpassAdapter:
    """Explicit, cached OSM lookups. A location rounded to 3 decimals (about 110 m) is sent to the
    chosen endpoint once; afterwards the vault's copy answers, also offline."""

    def __init__(self, cache: Path, url: str = "https://overpass-api.de/api/interpreter") -> None:
        self.cache = cache
        self.url = url

    def buildings(self, lat: float, lon: float, radius_m: int = 40) -> dict:
        """Buildings (ways and multipolygon relations) within ``radius_m``, with their geometry."""
        return self._ask(lat, lon, radius_m, 'way["building"]({at});relation["building"]["type"="multipolygon"]({at});', "")

    def streets(self, lat: float, lon: float, radius_m: int = 40) -> dict:
        """Named streets within ``radius_m``, with their geometry."""
        return self._ask(lat, lon, radius_m, f'way["highway"~"{STREETS}"]["name"]({{at}});', "streets-")

    def _ask(self, lat: float, lon: float, radius_m: int, wanted: str, kind: str) -> dict:
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Invalid location")
        where = f"around:{int(radius_m)},{lat:.3f},{lon:.3f}"
        query = f"[out:json][timeout:60];({wanted.format(at=where)});out geom tags;"
        # Buildings keep the key they had before streets were asked for: the vault's copies stay valid.
        path = self.cache / f"{kind}{hashlib.sha256(f'{self.url}:{where}'.encode()).hexdigest()}.json"
        if path.exists():
            return read_json(path)
        response = httpx.post(self.url, data={"data": query}, timeout=90, headers=AGENT)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data.get("elements"), list):
            raise ValueError("Unexpected Overpass response")
        atomic_text(path, response.text)
        return data

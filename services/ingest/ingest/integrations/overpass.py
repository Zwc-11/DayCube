import hashlib
import math
from pathlib import Path

import httpx

from ..vault import atomic_text, read_json


class OverpassAdapter:
    """Explicit, cached OSM building lookup. A location rounded to 3 decimals (about 110 m) is sent
    to the chosen endpoint once; afterwards the vault's copy answers, also offline."""

    def __init__(self, cache: Path, url: str = "https://overpass-api.de/api/interpreter") -> None:
        self.cache = cache
        self.url = url

    def buildings(self, lat: float, lon: float, radius_m: int = 40) -> dict:
        """Buildings (ways and multipolygon relations) within ``radius_m``, with their geometry."""
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Invalid location")
        where = f"around:{int(radius_m)},{lat:.3f},{lon:.3f}"
        key = hashlib.sha256(f"{self.url}:{where}".encode()).hexdigest()
        path = self.cache / f"{key}.json"
        if path.exists():
            return read_json(path)
        wanted = f'way["building"]({where});relation["building"]["type"="multipolygon"]({where});'
        query = f"[out:json][timeout:60];({wanted});out geom tags;"
        # Overpass answers 406 to a request without a User-Agent.
        response = httpx.post(
            self.url, data={"data": query}, timeout=90, headers={"User-Agent": "DAYCUBE/0.1 local-development"}
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data.get("elements"), list):
            raise ValueError("Unexpected Overpass response")
        atomic_text(path, response.text)
        return data

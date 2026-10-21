import hashlib
import math
from pathlib import Path

import httpx

from ..vault import atomic_text, read_json


class OverpassAdapter:
    """Explicit, cached OSM building lookup. Location is sent to the chosen endpoint."""

    def __init__(self, cache: Path, url: str = "https://overpass-api.de/api/interpreter") -> None:
        self.cache = cache
        self.url = url

    def buildings(self, lat: float, lon: float) -> dict:
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Invalid location")
        key = hashlib.sha256(f"{self.url}:{lat:.5f},{lon:.5f}".encode()).hexdigest()
        path = self.cache / f"{key}.json"
        if path.exists():
            return read_json(path)
        query = f'[out:json][timeout:25];way(around:40,{lat:.5f},{lon:.5f})["building"];out geom tags;'
        response = httpx.post(
            self.url, data={"data": query}, timeout=30, headers={"User-Agent": "DAYCUBE/0.1 local-development"}
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data.get("elements"), list):
            raise ValueError("Unexpected Overpass response")
        atomic_text(path, response.text)
        return data

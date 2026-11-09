import hashlib
from pathlib import Path

import httpx

from ..vault import atomic_text, read_json
from .overpass import AGENT


class NominatimAdapter:
    """A place's name to where it is, with its outline when it is a building or an area (OSM's
    Nominatim, ODbL). Asked once per name; afterwards the vault's copy answers, also offline.
    Nominatim's policy: an identifying User-Agent, at most one request a second, results kept."""

    def __init__(self, cache: Path, url: str = "https://nominatim.openstreetmap.org/search") -> None:
        self.cache = cache
        self.url = url

    def place(self, name: str) -> dict:
        """The best match: ``lat``, ``lon``, ``name``, ``osm`` ("way/14744491") and ``geojson``."""
        name = " ".join(name.split())
        if not name or len(name) > 200:
            raise ValueError("Invalid place name")
        path = self.cache / f"{hashlib.sha256(f'{self.url}:{name.casefold()}'.encode()).hexdigest()}.json"
        if not path.exists():
            params = {"q": name, "format": "jsonv2", "polygon_geojson": 1, "limit": 1}
            response = httpx.get(self.url, params=params, timeout=30, headers=AGENT)
            response.raise_for_status()
            if not isinstance(response.json(), list):
                raise ValueError("Unexpected Nominatim response")
            atomic_text(path, response.text)
        found = read_json(path)
        if not found:
            raise LookupError(f"No place called {name!r}")
        top = found[0]
        return {"lat": float(top["lat"]), "lon": float(top["lon"]), "name": top.get("display_name") or name,
                "osm": f"{top.get('osm_type')}/{top.get('osm_id')}", "geojson": top.get("geojson")}

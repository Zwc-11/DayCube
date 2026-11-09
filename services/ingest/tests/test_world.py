"""Worlds made from videos (ingest/world, docs/world.md): the store, the routes, OpenStreetMap
buildings and the ground's height. No network: Overpass and Open-Meteo are stood in for."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from ingest.api import create_app
from ingest.config import Settings
from ingest.integrations import nominatim as nominatim_module
from ingest.integrations import overpass as overpass_module
from ingest.vault import Vault
from ingest.world import geo, osm, store
from PIL import Image

DATE = "2026-11-05"


def world(wid: str, islands: int = 1, made: str = "2026-11-08T17:00:00-05:00") -> dict:
    enu = [[1, 0, 0, 5], [0, 1, 0, 0], [0, 0, 1, -3], [0, 0, 0, 1]]
    return {
        "id": wid, "date": DATE, "title": "Front St at dusk", "made": made,
        "source": {"file": "vlog.mp4", "t0_s": 100.0, "t1_s": 122.0},
        "origin": {"lat": 43.6447, "lon": -79.3801, "ground_m": 52.7},
        "islands": [{"id": f"is_{k + 1:02d}", "splat": f"is_{k + 1:02d}.splat", "points": 2, "enu": enu,
                     "bounds": {"min": [0, 0, -5], "max": [10, 4, 0]}, "located_by": "landmark+footprints",
                     "error_m": 4.0, "kind": "seen", "shots": [[100.0, 108.5]]} for k in range(islands)],
    }  # fmt: skip


def save(vault: Vault, w: dict) -> Path:
    return store.save(vault, DATE, w, {i["splat"]: bytes(64) for i in w["islands"]})


def test_a_world_is_saved_whole_and_listed_newest_first(vault: Vault) -> None:
    first, second = store.new_id(), store.new_id()
    assert store.WORLD.fullmatch(first) and first != second
    save(vault, world(first, made="2026-11-08T17:00:00-05:00"))
    path = save(vault, world(second, islands=2, made="2026-11-08T18:00:00-05:00"))
    assert sorted(p.name for p in path.iterdir()) == ["is_01.splat", "is_02.splat", "world.json"]
    assert store.load(vault, DATE, second) == world(second, islands=2, made="2026-11-08T18:00:00-05:00")
    assert store.load(vault, DATE, "wd_000000000000") is None
    listed = store.list_worlds(vault)
    assert [(w["id"], w["islands"]) for w in listed] == [(second, 2), (first, 1)]
    assert set(listed[0]) == {"date", "id", "title", "islands", "origin"}


@pytest.mark.parametrize(
    "change",
    [
        lambda w: w.update(id="wd_XYZ"),
        lambda w: w.update(date="2026-11-06"),
        lambda w: w["origin"].update(lat=91),
        lambda w: w["islands"][0].update(splat="../escape.splat"),
        lambda w: w["islands"][0].update(kind="google"),
        lambda w: w["islands"][0].update(located_by="guess"),
        lambda w: w["islands"][0]["enu"].__setitem__(3, [0, 0, 1, 1]),
        lambda w: w["islands"][0].update(bounds={"min": [1, 0, 0], "max": [0, 0, 0]}),
        lambda w: w["islands"][0].update(shots=[[5.0, 1.0]]),
        lambda w: w["islands"][0].update(error_m=-1),
        lambda w: w["islands"].append(dict(w["islands"][0])),  # the same island twice
    ],
)
def test_a_world_that_breaks_its_shape_is_refused(vault: Vault, change: Callable[[dict], object]) -> None:
    w = world(store.new_id())
    change(w)
    with pytest.raises(ValueError, match="Invalid world"):
        store.save(vault, DATE, w, {i["splat"]: bytes(64) for i in w["islands"] if store.ISLAND.fullmatch(i["id"])})
    assert not list(vault.root.glob(f"{DATE}/world/*/world.json"))


def test_splats_must_be_the_islands_whole_records(vault: Vault) -> None:
    w = world(store.new_id())
    for splats in ({"is_01.splat": bytes(63)}, {"is_01.splat": bytes(96)}, {"is_01.splat": bytes(64), "x.splat": bytes(32)}):
        with pytest.raises(ValueError, match="Invalid world"):
            store.save(vault, DATE, w, splats)


def test_the_routes_serve_a_world_and_only_its_islands(client: TestClient, vault: Vault) -> None:
    wid = store.new_id()
    save(vault, world(wid))
    (vault.root / DATE / "world" / wid / "notes.txt").write_text("not an island", encoding="utf-8")
    assert [w["id"] for w in client.get("/worlds").json()] == [wid]
    assert client.get(f"/worlds/{DATE}/{wid}").json()["islands"][0]["located_by"] == "landmark+footprints"
    splat = client.get(f"/worlds/{DATE}/{wid}/is_01.splat")
    assert splat.status_code == 200 and splat.content == bytes(64)
    assert splat.headers["content-type"] == "application/octet-stream"
    for bad in (f"/worlds/{DATE}/{wid}/world.json", f"/worlds/{DATE}/{wid}/notes.txt", f"/worlds/{DATE}/{wid}/is_02.splat",
                f"/worlds/{DATE}/wd_000000000000", f"/worlds/2026-13-40/{wid}", f"/worlds/{DATE}/..%2F..%2Fx",
                f"/worlds/{DATE}/{wid}/..%2F..%2F.token"):  # fmt: skip
        assert client.get(bad).status_code == 404, bad
    assert client.get("/health").json()["capabilities"]["world"] is True


OVERPASS = {
    "elements": [
        {"type": "way", "id": 1, "tags": {"building": "yes", "name": "Tall", "height": "40 m", "min_height": "3"},
         "geometry": [{"lat": 43.645, "lon": -79.381}, {"lat": 43.645, "lon": -79.380}, {"lat": 43.646, "lon": -79.380}]},
        {"type": "way", "id": 2, "tags": {"building": "office", "building:levels": "5", "building:min_level": "1"},
         "geometry": [{"lat": 43.644, "lon": -79.381}, {"lat": 43.644, "lon": -79.380}, {"lat": 43.6445, "lon": -79.380},
                      {"lat": 43.644, "lon": -79.381}]},
        {"type": "way", "id": 3, "tags": {"building": "yes", "height": "100 ft"},
         "geometry": [{"lat": 43.6, "lon": -79.3}, {"lat": 43.6, "lon": -79.31}, {"lat": 43.61, "lon": -79.31}]},
        {"type": "relation", "id": 9, "tags": {"building": "yes", "type": "multipolygon"},
         "members": [{"role": "outer", "geometry": [{"lat": 1, "lon": 1}, {"lat": 1, "lon": 2}, {"lat": 2, "lon": 2}, {"lat": 1, "lon": 1}]},
                     {"role": "inner", "geometry": [{"lat": 1.2, "lon": 1.2}, {"lat": 1.2, "lon": 1.4}, {"lat": 1.4, "lon": 1.4}]},
                     {"role": "outer", "geometry": [{"lat": 5, "lon": 5}, {"lat": 6, "lon": 6}]}]},
        {"type": "node", "id": 4, "lat": 43.6, "lon": -79.3},
    ]
}  # fmt: skip


class Answer:
    def __init__(self, data: dict) -> None:
        self.text = json.dumps(data)
        self._data = data
        self.status_code = 200

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        return None


def test_osm_buildings_become_footprints_with_heights() -> None:
    fc = osm.footprints(OVERPASS)
    assert fc["attribution"] == "© OpenStreetMap contributors, ODbL 1.0"
    props = {f["properties"]["id"]: f["properties"] for f in fc["features"]}
    assert list(props) == ["way/1", "way/2", "way/3", "relation/9"]  # a node is no building; the open outer is left out
    assert (props["way/1"]["height_m"], props["way/1"]["min_height_m"], props["way/1"]["height_from"]) == (40.0, 3.0, "height")
    assert (props["way/2"]["height_m"], props["way/2"]["min_height_m"], props["way/2"]["height_from"]) == (16.0, 3.2, "levels")
    assert props["way/3"]["height_m"] == pytest.approx(30.48)
    assert props["relation/9"]["height_from"] == "default" and props["relation/9"]["height_m"] == osm.DEFAULT_M
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] == [-79.381, 43.645] and len(ring) == 4  # closed, lon first
    assert [osm.metres(v) for v in ("12", "12.5 m", "40'", "tall", None)] == [12.0, 12.5, pytest.approx(12.192), None, None]


@pytest.fixture
def overpass_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def post(url: str, data: dict, timeout: float, headers: dict) -> Answer:
        assert headers["User-Agent"].startswith("DAYCUBE/")  # Overpass refuses requests without one
        calls.append(data["data"])
        return Answer(OVERPASS)

    monkeypatch.setattr(overpass_module.httpx, "post", post)
    return calls


def test_buildings_are_fetched_once_and_kept_for_offline(client: TestClient, vault: Vault, overpass_calls: list[str],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    first = client.get("/maps/buildings", params={"lat": 43.64471, "lon": -79.38012, "radius_m": 300})
    assert first.status_code == 200 and len(first.json()["features"]) == 4
    assert len(overpass_calls) == 1 and "around:300,43.645,-79.380" in overpass_calls[0]  # rounded: about 110 m
    assert "relation" in overpass_calls[0] and "out geom tags" in overpass_calls[0]
    assert list((vault.root / "maps" / "osm").glob("*.json"))

    def offline(*args: object, **kwargs: object) -> None:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(overpass_module.httpx, "post", offline)
    assert client.get("/maps/buildings", params={"lat": 43.6447, "lon": -79.3801, "radius_m": 300}).json() == first.json()
    missing = client.get("/maps/buildings", params={"lat": 43.70, "lon": -79.40})
    assert missing.status_code == 503 and "not reachable" in missing.json()["detail"]
    for bad in ({"lat": 91, "lon": 0}, {"lat": 0, "lon": 0, "radius_m": 20}, {"lat": 0, "lon": 0, "radius_m": 601}):
        assert client.get("/maps/buildings", params=bad).status_code == 422


def test_streets_are_asked_for_by_name_and_kept_apart_from_buildings(tmp_path: Path, overpass_calls: list[str],
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    adapter = overpass_module.OverpassAdapter(tmp_path)
    adapter.buildings(43.6447, -79.3801, 265)
    adapter.streets(43.6447, -79.3801, 265)
    assert '"highway"~"^(primary|' in overpass_calls[1] and '["name"]' in overpass_calls[1]
    assert "footway" not in overpass_calls[1]  # paths and sidewalks are not streets a wall faces
    assert sorted(p.name.startswith("streets-") for p in tmp_path.iterdir()) == [False, True]  # its own file
    adapter.streets(43.6447, -79.3801, 265)
    assert len(overpass_calls) == 2  # the vault's copy answers the second time


def test_a_place_is_looked_up_once_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    found = [{"lat": "43.6447091", "lon": "-79.3801352", "osm_type": "way", "osm_id": 14744491, "display_name": "Union Station",
              "geojson": {"type": "Polygon", "coordinates": [[[-79.38, 43.64], [-79.37, 43.64], [-79.37, 43.65], [-79.38, 43.64]]]}}]

    def get(url: str, params: dict, timeout: float, headers: dict) -> Answer:
        assert headers["User-Agent"].startswith("DAYCUBE/")  # Nominatim's policy: an identifying agent
        calls.append(params)
        return Answer(found if params["q"] != "Nowhere" else [])  # type: ignore[arg-type]

    monkeypatch.setattr(nominatim_module.httpx, "get", get)
    adapter = nominatim_module.NominatimAdapter(tmp_path)
    place = adapter.place("Union  Station, Toronto")
    assert place["osm"] == "way/14744491" and place["lat"] == pytest.approx(43.6447091) and place["geojson"]["type"] == "Polygon"
    assert calls[0]["polygon_geojson"] == 1 and calls[0]["limit"] == 1
    assert adapter.place("union station, toronto") == place and len(calls) == 1  # kept: case and spaces aside
    with pytest.raises(LookupError):
        adapter.place("Nowhere")


def test_the_google_key_needs_the_vault_token_and_is_null_without_one(vault: Vault, client: TestClient,
                                                                      auth: dict[str, str]) -> None:  # fmt: skip
    assert client.get("/maps/config").status_code == 401
    assert client.get("/maps/config", headers=auth).json() == {"google_tiles_key": None}
    settings = Settings(vault_root=vault.root, vector_search=False, google_maps_key="AIza-test", _env_file=None)
    with TestClient(create_app(settings)) as keyed:
        assert keyed.get("/maps/config", headers=auth).json() == {"google_tiles_key": "AIza-test"}


def test_the_local_frame_is_metres_east_and_south() -> None:
    lat0, lon0 = 43.6447, -79.3801
    north, east = geo._radii(lat0)
    x, z = geo.local_from_lat_lon(lat0, lon0, lat0 + 100 / (north * 3.141592653589793 / 180), lon0)
    assert abs(x) < 1e-9 and abs(z + 100) < 0.5  # 100 m north is z = -100
    x, z = geo.local_from_lat_lon(lat0, lon0, lat0, lon0 + 0.001)
    assert 80 < x < 81 and abs(z) < 1e-9  # 0.001 deg of longitude at 43.6 N is about 80.5 m
    for dx, dz in ((250.0, -1200.0), (-900.0, 40.0)):
        lat, lon = geo.lat_lon_from_local(lat0, lon0, dx, dz)
        assert geo.local_from_lat_lon(lat0, lon0, lat, lon) == pytest.approx((dx, dz), abs=1e-6)


def synthetic_geoid(path: Path) -> Path:
    """A global point grid at 45 degrees: 8 columns from 180 W, 5 rows from 90 N to 90 S, its value
    10 x row + col, so bilinear reading is exact."""
    image = Image.new("F", (8, 5))
    for row in range(5):
        for col in range(8):
            image.putpixel((col, row), float(10 * row + col))
    image.save(path)
    geo._grid.cache_clear()
    return path


def test_the_geoid_is_read_between_its_points(tmp_path: Path) -> None:
    grid = synthetic_geoid(tmp_path / geo.GEOID_FILE)
    assert geo.undulation(grid, 90, -180) == 0
    assert geo.undulation(grid, 0, 0) == pytest.approx(10 * 2 + 4)  # row 2 (equator), col 4 (0 E)
    assert geo.undulation(grid, 22.5, -157.5) == pytest.approx(10 * 1.5 + 0.5)
    assert geo.undulation(grid, -90, 135) == pytest.approx(10 * 4 + 7)
    # Past 135 E it wraps to the first column (180 W): halfway from col 7 to col 0.
    assert geo.undulation(grid, 0, 157.5) == pytest.approx(10 * 2 + 3.5)


def test_the_ground_height_is_the_dem_plus_the_geoid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synthetic_geoid(tmp_path / geo.GEOID_FILE)  # already kept: not fetched again
    asked: list[dict] = []

    def get(url: str, params: dict | None = None, **kwargs: object) -> Answer:
        assert url == geo.ELEVATION_URL
        asked.append(params or {})
        return Answer({"elevation": [90.0]})

    monkeypatch.setattr(geo.httpx, "get", get)
    assert geo.ground_height(0.0, 0.0, tmp_path) == pytest.approx(90.0 + 24.0)
    assert asked == [{"latitude": "0.00000", "longitude": "0.00000"}]


def test_inside_canada_the_ground_is_the_bare_earth_model(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def get(url: str, params: dict | None = None, **kwargs: object) -> Answer:
        asked.append(url)
        if url == geo.CANADA_URL:
            return Answer({"altitude": 81.0})
        return Answer({"elevation": [83.0]})

    monkeypatch.setattr(geo.httpx, "get", get)
    assert geo.elevation(43.64523, -79.38193) == 81.0  # Front St, Toronto: CDEM, not the rooftops
    assert geo.elevation(48.8584, 2.2945) == 83.0  # Paris: GLO-90
    assert asked == [geo.CANADA_URL, geo.ELEVATION_URL]


def test_an_island_may_say_where_it_was_filmed_from() -> None:
    w = world(store.new_id())
    w["islands"][0]["view"] = {"eye": [0, 1.45, 0], "look": [10, 1.45, 0]}
    store.validate(w, DATE)
    for bad in ({"eye": [0, 1.45]}, {"eye": [0, 1, 0], "look": [0, 1, 0], "up": [0, 1, 0]}, {"eye": [0, "1", 0], "look": [1, 1, 1]}):
        w["islands"][0]["view"] = bad
        with pytest.raises(ValueError, match="view"):
            store.validate(w, DATE)

"""Which wall a shot was filmed beside (ingest/world/walls.py), on made-up blocks: no network."""

from __future__ import annotations

import math

import numpy as np
import pytest
from ingest.recon.island import choose
from ingest.world.geo import lat_lon_from_local, local_from_lat_lon
from ingest.world.walls import ANY, Seen, landmark_walls, straight_runs

ORIGIN = (43.645, -79.380)


def ring(*xz: tuple[float, float]) -> list[list[float]]:
    """A closed (lon, lat) ring from local corners (x east, z south)."""
    corners = [lat_lon_from_local(*ORIGIN, x, z) for x, z in [*xz, xz[0]]]
    return [[lon, lat] for lat, lon in corners]


def building(ident: str, *xz: tuple[float, float]) -> dict:
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring(*xz)]}, "properties": {"id": ident}}


def street(name: str, *xz: tuple[float, float]) -> dict:
    return {"type": "way", "tags": {"name": name}, "geometry": [dict(zip(("lat", "lon"), lat_lon_from_local(*ORIGIN, x, z), strict=True)) for x, z in xz]}


# A station 60 m east-west by 20 m, Front St 12 m north of it; a kiosk on Front St's sidewalk.
STATION = building("way/1", (0, 0), (60, 0), (60, 20), (0, 20))
KIOSK = building("way/2", (40, -4), (46, -4), (46, -1), (40, -1))
FRONT = street("Front Street West", (-50, -12), (110, -12))
PLACE = {"lat": lat_lon_from_local(*ORIGIN, 30, 10)[0], "lon": lat_lon_from_local(*ORIGIN, 30, 10)[1], "osm": "way/1", "name": "Station"}


def local(wall_end: tuple[float, float]) -> tuple[float, float]:
    x, z = local_from_lat_lon(*ORIGIN, *wall_end)
    return round(x, 2), round(z, 2)


def test_the_wall_on_the_street_is_found_walked_with_the_building_on_the_right() -> None:
    found = landmark_walls(PLACE, {"features": [STATION]}, {"elements": [FRONT]}, Seen(3.0, 5.0, 20.0))
    assert len(found) == 4 and found[0].street == "Front Street West"
    front = found[0]
    assert (local(front.a), local(front.b)) == ((0, 0), (60, 0))  # walking east, the station to the south
    assert front.length == 60.0 and front.along == pytest.approx((-7.0, 42.0))  # the facade seen 5-20 m ahead fits
    assert [w.street for w in found[1:]] == [None, None, None]
    assert choose(found, None, None) is front  # the only one facing a street


def test_a_building_on_the_sidewalk_leaves_only_where_the_walk_could_be() -> None:
    found = landmark_walls(PLACE, {"features": [STATION, KIOSK]}, {"elements": [FRONT]}, Seen(3.0, 5.0, 20.0))
    front = found[0]
    # The kiosk stands 1-4 m out, 40-46 m along: a walk 3 m out cannot pass it within 20 m ahead.
    assert front.along[0] == pytest.approx(-7.0) and front.along[1] < 40 - 20 + 1


def test_a_facade_longer_than_the_wall_does_not_fit() -> None:
    found = landmark_walls(PLACE, {"features": [STATION]}, {"elements": [FRONT]}, Seen(3.0, 0.0, 70.0))
    assert not found  # 60 m and 20 m walls


def test_nearly_straight_edges_are_one_wall_and_a_jog_is_not() -> None:
    pts = np.array([[0, 0], [30, 0.3], [60, 0], [60, 20], [33, 20], [33, 23], [0, 23], [0, 0]], float)
    runs = [(tuple(a.round(1)), tuple(b.round(1))) for a, b in straight_runs(pts)]
    assert ((0.0, 0.0), (60.0, 0.0)) in runs or ((60.0, 0.0), (0.0, 0.0)) in runs
    assert len(runs) == 6  # the north side whole; the south side's 3 m jog keeps its three edges


def test_either_winding_gives_the_same_walls() -> None:
    reverse = {**STATION, "geometry": {"type": "Polygon", "coordinates": [STATION["geometry"]["coordinates"][0][::-1]]}}
    a = landmark_walls(PLACE, {"features": [STATION]}, {"elements": [FRONT]}, ANY)
    b = landmark_walls(PLACE, {"features": [reverse]}, {"elements": [FRONT]}, ANY)
    assert len(a) == len(b) == 4 and np.allclose(sorted((*w.a, *w.b) for w in a), sorted((*w.a, *w.b) for w in b))


def test_a_symmetric_building_is_never_guessed() -> None:
    # Two wings on the same street: both fit, so both are listed and the user picks.
    wings = building("way/1", (0, 0), (40, 0), (40, -8), (80, -8), (80, 0), (120, 0), (120, 20), (0, 20))
    found = landmark_walls(PLACE, {"features": [wings]}, {"elements": [street("Front Street West", (-50, -20), (170, -20))]}, ANY)
    asked = choose(found, "front", None)
    assert isinstance(asked, str) and asked.startswith("3 walls fit") and "--pick" in asked
    picked = choose(found, "front", 2)
    assert not isinstance(picked, str) and picked.street == "Front Street West" and math.isclose(picked.length, 40.0)
    assert choose(found, "King", None).startswith("No wall fits on 'King'")

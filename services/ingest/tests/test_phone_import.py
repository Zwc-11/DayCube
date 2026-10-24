"""Phone recordings into the vault (ingest.phone): a synthetic afternoon recorded the way the apps
export it. Home for five minutes, a ten-minute drive, a walk around a park, the drive back, upstairs
at home; a voice memo over the drive back; a LiDAR scan during the walk."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import shutil
import subprocess
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ingest.api import create_app
from ingest.config import Settings
from ingest.phone import inbox, sensor_logger, stray, visits
from ingest.phone.geo import EARTH_M
from ingest.phone.importer import import_phone
from ingest.vault import Vault, read_json, read_lines

START = datetime(2026, 10, 24, 19, 0, 0, tzinfo=UTC)  # 15:00 in Toronto
HOME = (45.50, -73.57)
PARK = (45.58, -73.57)  # 8.9 km north
FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="FFmpeg is not installed")


def offset(origin: tuple[float, float], east: float, north: float) -> tuple[float, float]:
    lat = origin[0] + math.degrees(north / EARTH_M)
    return lat, origin[1] + math.degrees(east / (EARTH_M * math.cos(math.radians(origin[0]))))


def walk_at(s: float) -> tuple[float, float]:
    """Metres east and north of the park's middle, s seconds into the walk: a 200 m circle."""
    a = s * 1.3 / 200.0
    return 200.0 * math.sin(a), -200.0 * math.cos(a) + 200.0


def afternoon() -> list[tuple[float, float, float, str, float]]:
    """(seconds from START, lat, lon, activity, relative altitude) each second."""
    rows = []
    legs = [("stationary", 300), ("automotive", 600), ("walking", 900), ("automotive", 600), ("stationary", 300)]
    t = 0.0
    for activity, seconds in legs:
        for k in range(seconds):
            share = k / seconds
            if activity == "stationary":
                lat, lon = HOME
            elif activity == "automotive":
                a, b = (HOME, PARK) if t < 1000 else (PARK, HOME)
                lat, lon = a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share
            else:
                lat, lon = offset(PARK, *walk_at(k))
            upstairs = 3.3 if t >= 2420 else 0.0  # up the stairs 20 s after coming home
            rows.append((t, lat, lon, activity, upstairs))
            t += 1.0
    return rows


def sensor_zip(folder: Path) -> Path:
    rows = afternoon()
    ns = lambda s: str(int((START.timestamp() + s) * 1e9))  # noqa: E731

    def table(header: list[str], body: list[list]) -> str:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(header)
        writer.writerows(body)
        return out.getvalue()

    files = {
        "Metadata.csv": table(
            ["version", "device name", "recording epoch time", "recording time", "recording timezone"],
            [[3, "Test Phone", int(START.timestamp() * 1000), "2026-10-24_19-00-00", "America/Toronto"]],
        ),
        "Location.csv": table(
            [
                "time",
                "seconds_elapsed",
                "altitude",
                "speedAccuracy",
                "bearingAccuracy",
                "latitude",
                "altitudeAboveMeanSeaLevel",
                "bearing",
                "horizontalAccuracy",
                "verticalAccuracy",
                "longitude",
                "speed",
            ],
            [[ns(t), t, 150, -1, -1, lat, 180, -1, 8.0, 10, lon, -1] for t, lat, lon, _, _ in rows[::2]],
        ),  # fmt: skip
        "Barometer.csv": table(
            ["time", "seconds_elapsed", "relativeAltitude", "pressure"],
            [[ns(t), t, alt, 1010.0] for t, *_, alt in rows],
        ),  # fmt: skip
        "Pedometer.csv": table(
            ["time", "seconds_elapsed", "steps"],
            [[ns(t), t, int(max(0, t - 900) * 1.6) if t < 1800 else 1440] for t, *_ in rows[::10]],
        ),  # fmt: skip
        "Activity.csv": table(
            ["time", "seconds_elapsed", "activity"], [[ns(t), t, activity] for t, _, _, activity, _ in rows[::15]]
        ),  # fmt: skip
        "Magnetometer.csv": table(["time", "seconds_elapsed", "z", "y", "x"], [[ns(0), 0, 1, 2, 3]]),
    }
    path = folder / "1 Main St-2026-10-24_19-00-00.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return path


def voice_memo(folder: Path) -> tuple[Path, Path]:
    """A 30 s memo saved (stamped) 20 s after coming home, and its transcript beside it."""
    end = START + timedelta(seconds=2420)
    path = folder / "Main St 3.m4a"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=30", "-c:a", "aac",
         "-metadata", f"creation_time={end.strftime('%Y-%m-%dT%H:%M:%S.000000Z')}", str(path)],
        check=True,
    )  # fmt: skip
    transcript = {"segments": [
        {"start": 1.0, "end": 3.5, "text": " Almost home.", "words": [
            {"start": 1.0, "end": 1.6, "word": " Almost", "p": 0.9}, {"start": 1.7, "end": 3.5, "word": " home.", "p": 0.8}]},
        {"start": 10.0, "end": 12.0, "text": " The park was quiet.", "words": []},
    ]}  # fmt: skip
    beside = folder / "Main St 3.json"
    beside.write_text(json.dumps(transcript))
    return path, beside


def scan(folder: Path, rotation_deg: float = 30.0) -> Path:
    """A 12 s Stray Scanner capture 120 s into the walk; its ARKit frame turned ``rotation_deg``
    from east-north."""
    capture = folder / "scan"
    capture.mkdir()
    began = START + timedelta(seconds=900 + 120)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=30:duration=12",
         "-pix_fmt", "yuv420p", "-metadata", f"creation_time={began.strftime('%Y-%m-%dT%H:%M:%S.000000Z')}",
         str(capture / "rgb.mp4")],
        check=True,
    )  # fmt: skip
    a = math.radians(rotation_deg)
    with (capture / "odometry.csv").open("w", newline="") as out:
        out.write("timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy\n")
        e0, n0 = walk_at(120)
        for k in range(360):
            e, n = walk_at(120 + k / 30)
            de, dn = e - e0, n - n0
            # ARKit (x, -z) is east-north turned back by the rotation.
            x, fwd = de * math.cos(a) + dn * math.sin(a), -de * math.sin(a) + dn * math.cos(a)
            out.write(f"{1000 + k / 30:.4f}, {k:06d}, {x:.4f}, 0.0, {-fwd:.4f}, 0, 0, 0, 1, 450.0, 450.0, 320, 240\n")
    (capture / "camera_matrix.csv").write_text("450.0, 0.0, 320\n0.0, 450.0, 240\n0.0, 0.0, 1.0\n")
    return capture


def test_a_recording_is_split_into_stays_and_trips(tmp_path: Path) -> None:
    recording = sensor_logger.parse(sensor_zip(tmp_path))
    assert recording.tz == "America/Toronto" and recording.name == "1 Main St" and recording.device == "Test Phone"
    spans = visits.segment(recording)
    assert [(s.kind, s.mode) for s in spans] == [
        ("stay", "house"), ("trip", "vehicle"), ("stay", "outdoor"), ("trip", "vehicle"), ("stay", "house"),
    ]  # fmt: skip
    walk = spans[2]
    assert abs((walk.t1 - walk.t0) - 900) < 60 and 1000 < walk.distance_m < 1300 and walk.steps > 1000
    assert all(abs(t.distance_m - 8900) < 300 for t in spans[1::2])


@needs_ffmpeg
def test_an_afternoon_of_recordings_becomes_a_vault_day(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    vault.initialize()
    drop = tmp_path / "drop"
    drop.mkdir()
    paths = [sensor_zip(drop), *voice_memo(drop), scan(drop)]
    said: list[str] = []
    result = import_phone(vault, paths, transcribe=False, say=said.append)
    assert list(result) == ["2026-10-24"]
    day = read_json(vault.root / "2026-10-24/day.json")
    places = day["places"]
    assert [(p["name"], p["kind"]) for p in places] == [
        ("1 Main St", "house"),
        ("Outdoors", "outdoor"),
        ("1 Main St", "house"),
    ]
    assert places[0]["t0"].startswith("2026-10-24T15:00") and places[2]["floors_visited"] == [0, 1]
    assert [(t["from"], t["to"], t["mode"]) for t in day["transits"]] == [
        (places[0]["id"], places[1]["id"], "vehicle"), (places[1]["id"], places[2]["id"], "vehicle"),
    ]  # fmt: skip
    events = read_lines(vault.root / "2026-10-24/events.jsonl")
    kinds = [e["type"] for e in events]
    assert kinds.count("place.visit") == 3 and kinds.count("place.transit") == 2 and kinds.count("place.floor") == 1
    memo = next(e for e in events if e["type"] == "moment.audio")
    # Stamped at its end (Voice Memos): it began on the drive, 10 s before coming home, and it is put
    # with the visit it overlaps most: home.
    assert memo["t"] == "2026-10-24T15:39:50-04:00" and memo["place"] == places[2]["id"]
    capture = next(e for e in events if e["type"] == "moment.video")
    assert capture["place"] == places[1]["id"]
    assert any("placed on the map" in line for line in said)
    folder = vault.root / "2026-10-24/moments" / capture["moment_id"]
    scene = read_json(folder / "scene.json")
    fit = scene["georeferenced"]
    assert abs(fit["rotation_deg"] - 30.0) < 2 and fit["rmse_m"] < 2 and abs(fit["offset_s"]) <= 1
    assert len(scene["keyframes"]) == 2 and all((folder / k["file"]).is_file() for k in scene["keyframes"])
    camera = read_json(folder / "camera.json")
    assert camera["points"][0]["lat"] == pytest.approx(offset(PARK, *walk_at(120))[0], abs=2e-5)
    assert "depth folder" in scene["reason"] and (folder / "video.mp4").is_file()
    transcript = read_json(vault.root / "2026-10-24/moments" / memo["moment_id"] / "transcript.json")
    assert [s["text"] for s in transcript["segments"]] == ["Almost home.", "The park was quiet."]
    assert transcript["segments"][0]["words"][0] == {"word": "Almost", "start": 1.0, "end": 1.6, "score": 0.9}
    skeleton = read_json(vault.root / "2026-10-24/skeleton.geojson")["features"]
    outdoor = [f["properties"] for f in skeleton if f["properties"]["place"] == places[1]["id"]]
    indoors = [f["properties"] for f in skeleton if f["properties"]["place"] == places[0]["id"]]
    assert outdoor and all(p["x"] is not None for p in outdoor) and all(p["x"] is None for p in indoors)
    assert vault.validate("2026-10-24")["valid"]

    # Something else recorded that day stays; importing again replaces only what the import wrote.
    other = {**events[0], "id": "ev_note", "type": "screen.app", "label": "Notes", "app": "notes.exe",
             "window_title": "Plan", "t_end": events[0]["t"], "source": {"kind": "capture", "ref": "capture:1"}}  # fmt: skip
    vault.append_events("2026-10-24", [other])
    import_phone(vault, paths, transcribe=False, say=lambda _: None)
    again = read_lines(vault.root / "2026-10-24/events.jsonl")
    assert len(again) == len(events) + 1 and "ev_note" in {e["id"] for e in again}
    assert len(read_json(vault.root / "2026-10-24/skeleton.geojson")["features"]) == len(skeleton)
    assert vault.validate("2026-10-24")["valid"]

    # The viewer's day: nothing drawn inside the house; the walk shrunk onto the stage.
    settings = Settings(vault_root=vault.root, vector_search=False, _env_file=None)
    with TestClient(create_app(settings)) as client:
        layers = client.get("/day/2026-10-24").json()["layers"]
        assert 1 <= len(layers[0]["path"]) <= 2 and all(p["x"] is None for p in layers[0]["path"])
        walk = layers[1]
        assert walk["frame"]["scale"] > 20 and max(abs(p["x"]) for p in walk["path"]) <= 9.5
        opened = client.get(f"/moment/{capture['moment_id']}", params={"date": "2026-10-24"}).json()
        assert opened["camera"]["fit"]["rmse_m"] < 2 and opened["scene"]["kind"] == "camera-path"
        audio = client.get(f"/vault/2026-10-24/moments/{memo['moment_id']}/audio.m4a")
        assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mp4"


@needs_ffmpeg
def test_the_inbox_imports_what_has_stopped_changing_and_does_not_retry_a_failure(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    vault.initialize()
    drop = inbox.folder(vault)
    sensor_zip(drop)
    assert inbox.process(vault, say=lambda _: None) is None  # just copied: wait for it to settle
    later = time.time() + inbox.SETTLE_S + 1
    result = inbox.process(vault, now=later, say=lambda _: None, transcribe=False)
    assert result["imported"] and list(result["days"]) == ["2026-10-24"]
    assert not any(p.suffix == ".zip" for p in drop.iterdir()) and list((drop / "imported").rglob("*.zip"))
    assert inbox.process(vault, now=later, say=lambda _: None) is None  # nothing left
    (drop / "notes.txt").write_text("not a recording")
    os.utime(drop / "notes.txt", (later - 100, later - 100))
    assert inbox.process(vault, now=later, say=lambda _: None)["imported"] is False
    assert inbox.process(vault, now=later, say=lambda _: None) is None  # failed before, unchanged
    assert "Nothing to import" in (drop / "errors.jsonl").read_text()


def test_a_scan_is_found_among_loose_attachment_files(tmp_path: Path) -> None:
    for name in ("84120dfe-odometry.csv", "4093546f-camera_matrix.csv", "rgb.mp4", "48a723e0-imu.csv"):
        (tmp_path / name).write_text("x")
    (found,) = stray.find(sorted(tmp_path.iterdir()))
    assert {k: v.name for k, v in found.items()} == {
        "video": "rgb.mp4", "odometry": "84120dfe-odometry.csv", "camera": "4093546f-camera_matrix.csv",
    }  # fmt: skip

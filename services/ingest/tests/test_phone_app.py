"""DayCube Capture's own files, through the receiver: hours of sensors in Sensor Logger's layout,
joined into one afternoon; a day keeping what an earlier recording gave it; and an hour of sound
cut into talk moments by the conversations job, each then queued for its speech."""

import csv
import hashlib
import io
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ingest.config import Settings
from ingest.phone import sources
from ingest.phone.importer import import_phone
from ingest.vault import Vault, read_json
from test_phone_import import START, afternoon, sensor_zip

FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="FFmpeg is not installed")


def _table(header: list[str], body: list[list]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(body)
    return out.getvalue()


def app_hours(folder: Path, cut_s: float = 1500.0) -> list[Path]:
    """The afternoon as DayCube Capture writes it: two recordings back to back, cut during the
    walk, each with its relative altitude and steps from 0, named by their UTC start."""
    rows = afternoon()
    out = []
    for part in (rows[: int(cut_s)], rows[int(cut_s) :]):
        t_first = part[0][0]
        start = START + timedelta(seconds=t_first)
        path = folder / f"sensors-{start.strftime('%Y%m%dT%H%M%SZ')}"
        path.mkdir(parents=True)
        ns = lambda s: str(int((START.timestamp() + s) * 1e9))  # noqa: E731
        base_alt = part[0][4]
        (path / "Metadata.csv").write_text(
            _table(["version", "device name", "recording timezone"], [[1, "Test Phone", "America/Toronto"]])
        )
        (path / "Location.csv").write_text(_table(
            ["time", "latitude", "longitude", "horizontalAccuracy", "altitude", "speed", "bearing"],
            [[ns(t), lat, lon, 8.0, 150, -1, -1] for t, lat, lon, _, _ in part[::2]]))  # fmt: skip
        (path / "Barometer.csv").write_text(_table(["time", "relativeAltitude", "pressure"],
                                                   [[ns(t), alt - base_alt, 1010.0] for t, *_, alt in part]))  # fmt: skip
        (path / "Activity.csv").write_text(_table(["time", "activity"], [[ns(t), a] for t, _, _, a, _ in part[::15]]))
        out.append(path)
    return out


def _places(vault: Vault) -> list[tuple[str, str]]:
    day = read_json(vault.root / "2026-10-24" / "day.json")
    return [(p["name"], p["kind"]) for p in day["places"]]


def test_the_apps_hours_are_one_afternoon_and_a_day_keeps_what_each_recording_gave_it(tmp_path: Path) -> None:
    reference = Vault(tmp_path / "reference")
    reference.initialize()
    (tmp_path / "zip").mkdir()
    import_phone(reference, [sensor_zip(tmp_path / "zip")], transcribe=False, say=lambda _: None)

    vault = Vault(tmp_path / "vault")
    vault.initialize()
    first, second = app_hours(tmp_path / "phone")
    import_phone(vault, [first], transcribe=False, say=lambda _: None)
    import_phone(vault, [second], transcribe=False, say=lambda _: None)  # an hour later, alone
    # The same visits as the one recording: the walk across the cut is one stay, the floor climb
    # after coming home (3.3 m) is kept though each hour's altitude starts at 0.
    assert [kind for _, kind in _places(vault)] == ["house", "outdoor", "house"]
    day = read_json(vault.root / "2026-10-24" / "day.json")
    assert day["places"][2]["floors_visited"] == [0, 1]
    assert len(day["transits"]) == 2
    assert sorted(p.name for p in (vault.root / "sources" / "sensors").iterdir() if p.is_dir()) == [
        "20261024T190000Z-sensors-20261024T190000Z", "20261024T192500Z-sensors-20261024T192500Z"]  # fmt: skip
    assert [p["kind"] for p in read_json(reference.root / "2026-10-24" / "day.json")["places"]] == [
        "house",
        "outdoor",
        "house",
    ]


def test_a_later_recording_of_the_same_day_adds_to_it(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    vault.initialize()
    (tmp_path / "a").mkdir()
    import_phone(vault, [sensor_zip(tmp_path / "a")], transcribe=False, say=lambda _: None)
    before = read_json(vault.root / "2026-10-24" / "day.json")["places"]
    # The evening: the same phone, three hours later, at home for 20 minutes.
    late = tmp_path / "late" / "sensors-20261024T230000Z"
    late.mkdir(parents=True)
    t0 = datetime(2026, 10, 24, 23, 0, tzinfo=UTC).timestamp()
    ns = lambda s: str(int((t0 + s) * 1e9))  # noqa: E731
    (late / "Metadata.csv").write_text(
        _table(["version", "device name", "recording timezone"], [[1, "Test Phone", "America/Toronto"]])
    )
    (late / "Location.csv").write_text(_table(["time", "latitude", "longitude", "horizontalAccuracy"],
                                              [[ns(s), 45.50, -73.57, 8.0] for s in range(0, 1200, 5)]))  # fmt: skip
    (late / "Activity.csv").write_text(
        _table(["time", "activity"], [[ns(s), "stationary"] for s in range(0, 1200, 60)])
    )
    import_phone(vault, [late], transcribe=False, say=lambda _: None)
    after = read_json(vault.root / "2026-10-24" / "day.json")["places"]
    assert [p["id"] for p in after[:3]] == [p["id"] for p in before] and len(after) == 4
    assert after[3]["t0"].startswith("2026-10-24T19:00")


def test_recordings_imported_before_they_were_kept_are_kept_from_the_inbox(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    vault.initialize()
    batch = vault.root / "inbox" / "phone" / "imported" / "20261025-024851"
    batch.mkdir(parents=True)
    sensor_zip(batch)
    assert sources.backfill(vault) == 1 and sources.backfill(vault) == 0
    assert len(sources.sensors_between(vault, START.timestamp(), START.timestamp() + 60)) == 1


@needs_ffmpeg
def test_an_hour_of_sound_from_the_phone_becomes_talk_moments_queued_for_speech(vault: Vault, tmp_path: Path) -> None:
    from capture import api as capture_api
    from capture.api import create_capture_app
    from ingest.jobs.store import Jobs
    from ingest.phone.inbox import process

    # Ten minutes of tone, as the app's AAC hour; the conversations job's answer is given by hand.
    hour = tmp_path / "audio.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=600", "-ac", "1", "-ar", "16000",
                    "-c:a", "aac", "-b:a", "32k", str(hour)], check=True)  # fmt: skip
    data = hour.read_bytes()
    settings = Settings(vault_root=vault.root, vector_search=False, _env_file=None, timezone="America/Toronto")
    monkey_idle = pytest.MonkeyPatch()
    monkey_idle.setattr(capture_api, "idle_seconds", lambda: 3600.0)
    try:
        with TestClient(create_capture_app(settings, receive=True)) as client:
            paired = client.post(
                "/phone/pair", json={"name": "phone"}, headers={"Authorization": f"Bearer {vault.token}"}
            ).json()
            auth = {"Authorization": f"Bearer {paired['token']}"}
            body = {"item": "audio-20261024T190000Z", "name": "audio-20261024T190000Z.m4a", "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest()}  # fmt: skip
            up = client.post("/phone/uploads", json=body, headers=auth).json()
            client.put(f"/phone/uploads/{up['id']}", params={"offset": 0}, content=data, headers=auth)
            client.post(f"/phone/uploads/{up['id']}/complete", headers=auth)
            done = client.post("/phone/items/complete", json={"item": body["item"]}, headers=auth).json()
            assert done["path"] == f"{paired['device']}-audio-20261024T190000Z.m4a"
            monkey_idle.setattr("ingest.phone.importer.vault_timezone", lambda v: "America/Toronto")
            result = process(vault, now=4e9, say=lambda _: None)
            assert result and result["imported"]
            kept = read_json(vault.root / "sources" / "audio" / "index.json")
            assert list(kept) == [f"sources/audio/{paired['device']}/20261024T190000Z.m4a"]

            # The speech worker leases the conversations job, finds two talks, and reports them.
            worker = {"worker": "speech@pc"}
            job = client.post(
                "/jobs/lease",
                json={**worker, "needs": "cpu", "stages": ["conversations"]},
                headers={"Authorization": f"Bearer {vault.token}"},
            ).json()["job"]
            assert job["inputs"]["audio"] == list(kept)[0] and job["inputs"]["t0"].startswith("2026-10-24T19:00:00")
            monkey_idle.setattr("ingest.config.Settings", lambda: settings)
            answer = {**worker, "outputs": {"spans": [[30.0, 95.5], [300.0, 420.0]]}}
            assert client.post(
                f"/jobs/{job['id']}/done", json=answer, headers={"Authorization": f"Bearer {vault.token}"}
            ).json()["ok"]
    finally:
        monkey_idle.undo()
    talks = sorted((vault.root / "2026-10-24" / "moments").glob("mo_talk_*"))
    assert [t.name for t in talks] == ["mo_talk_20261024_150030", "mo_talk_20261024_150500"]
    scene = read_json(talks[0] / "scene.json")
    assert scene["kind"] == "audio" and scene["clip"] == {"of": list(kept)[0], "start_s": 30.0, "end_s": 95.5}
    length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                   str(talks[0] / scene["audio"])], capture_output=True, text=True).stdout)  # fmt: skip
    assert length == pytest.approx(65.5, abs=0.2)
    meta = read_json(talks[0] / "meta.json")
    assert meta["t0"] == "2026-10-24T15:00:30-04:00" and meta["duration_s"] == pytest.approx(65.5)
    events = [json.loads(line) for line in (vault.root / "2026-10-24" / "events.jsonl").read_text().splitlines()]
    assert sum(e.get("moment_id", "").startswith("mo_talk_") for e in events) == 2
    jobs = Jobs.of(vault.root)
    try:
        for talk in talks:  # each queued for its speech, once
            speech = jobs.find("speech", {"date": "2026-10-24", "moment": talk.name})
            assert [j["status"] for j in speech] == ["queued"]
    finally:
        jobs.close()

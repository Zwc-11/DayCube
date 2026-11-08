"""Deleting a span of a day (DELETE /day/{date}/hours, ingest/forget.py): what it held leaves the
source files, the search database file, the notes and the review, and nothing written later can
bring it back."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import CAPTURE_DAY, DAY, capture_session
from fastapi.testclient import TestClient
from ingest.api import create_app
from ingest.config import Settings
from ingest.forget import _cut_csv, _cut_zip
from ingest.notes import Notes
from ingest.store import LifeStore
from ingest.time import instant
from ingest.vault import Vault, read_lines

GONE = "Wikipedia"  # only in the morning's 09:05-09:11 (the click, the page, the copy)


@pytest.fixture
def api(vault: Vault) -> Iterator[TestClient]:
    settings = Settings(vault_root=vault.root, vector_search=False, _env_file=None)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        client.headers["Authorization"] = f"Bearer {vault.token}"
        yield client


def delete(api: TestClient, day: str, t0: str, t1: str) -> object:
    return api.request("DELETE", f"/day/{day}/hours", json={"t0": f"{day}T{t0}-04:00", "t1": f"{day}T{t1}-04:00"})


def everything(vault: Vault) -> bytes:
    """Every byte the vault holds, so a deleted phrase can be looked for anywhere in it."""
    return b"".join(p.read_bytes() for p in sorted(vault.root.rglob("*")) if p.is_file())


def test_a_deleted_span_leaves_capture_search_notes_and_the_database_file(vault: Vault, api: TestClient) -> None:
    capture_session(vault)
    Notes(LifeStore(vault)).write_day(CAPTURE_DAY)
    api.post(f"/day/{CAPTURE_DAY}/review").raise_for_status()
    assert GONE.encode() in everything(vault)
    response = delete(api, CAPTURE_DAY, "09:05:00", "09:11:00")
    assert response.status_code == 200, response.text
    assert response.json()["removed"]["capture"] == 5  # the search, its page, the click, the page, the copy
    assert GONE.encode() not in everything(vault)  # logs, notes, review, timeline, daycube.sqlite and its WAL
    assert not api.get("/search", params={"q": "wikipedia"}).json()["evidence"]
    browser = read_lines(vault.root / "capture" / CAPTURE_DAY / "browser.jsonl")
    assert [r["t"][11:19] for r in browser] == [
        "09:00:00",
        "09:12:00",
        "09:20:00",
        "09:25:00",
        "09:25:10",
        "09:30:10",
        "09:31:00",
    ]
    assert api.get("/search", params={"q": "marmoset"}).json()["evidence"]  # what lies outside the span stays
    daily = (vault.root / "notes" / "Daily" / f"{CAPTURE_DAY}.md").read_text(encoding="utf-8")
    assert "CS 136" in daily and "linear probing" not in daily.lower()


def test_nothing_written_later_brings_the_span_back(vault: Vault, api: TestClient) -> None:
    capture_session(vault)
    delete(api, CAPTURE_DAY, "09:05:00", "09:11:00").raise_for_status()
    late = {"id": "late", "t": f"{CAPTURE_DAY}T09:06:00-04:00", "kind": "page", "url": "https://en.wikipedia.org/"}
    after = dict(late, id="after", t=f"{CAPTURE_DAY}T09:40:00-04:00")
    Vault(vault.root).append_lines(
        vault.root / "capture" / CAPTURE_DAY / "browser.jsonl", [late, after]
    )  # the receiver
    ids = [r["id"] for r in read_lines(vault.root / "capture" / CAPTURE_DAY / "browser.jsonl")]
    assert "after" in ids and "late" not in ids
    assert api.get(f"/day/{CAPTURE_DAY}/deletions").json()[0]["t0"] == f"{CAPTURE_DAY}T09:05:00-04:00"


def test_a_recording_in_the_span_goes_whole_and_the_day_stays_valid(vault: Vault, api: TestClient) -> None:
    folder = vault.day_path(DAY)
    meta = json.loads((folder / "moments" / "mo_001" / "meta.json").read_text(encoding="utf-8"))
    assert meta["t0"] == f"{DAY}T12:40:00-04:00"
    before = read_lines(folder / "events.jsonl")
    response = delete(api, DAY, "12:30:00", "12:41:00")
    assert response.status_code == 200, response.text
    assert response.json()["removed"]["recordings"] == 1
    assert not (folder / "moments" / "mo_001").exists()
    events = read_lines(folder / "events.jsonl")
    span = (instant(f"{DAY}T12:30:00-04:00"), instant(f"{DAY}T12:41:00-04:00"))
    assert all(not span[0] <= instant(e["t"]) < span[1] and e.get("moment_id") != "mo_001" for e in events)
    assert len(events) < len(before)
    graph = json.loads((folder / "graph.json").read_text(encoding="utf-8"))
    assert all(n["id"] != "mo_001" for n in graph["nodes"]) and all(
        "mo_001" not in json.dumps(e) for e in graph["edges"]
    )
    assert Vault(vault.root).validate(DAY)["valid"]
    assert api.get(f"/day/{DAY}").status_code == 200
    for place in json.loads((folder / "day.json").read_text(encoding="utf-8"))["places"]:
        a, b = instant(place["t0"]), instant(place["t1"])
        assert not (span[0] <= a < span[1] or span[0] < b <= span[1]) or (a < span[0] and b > span[1])


@pytest.mark.parametrize(
    ("t0", "t1"), [("10:00:00", "09:00:00"), ("23:30:00", "2026-10-21T00:30:00-04:00"), ("09:00:00", "09:00:00")]
)
def test_a_span_must_lie_in_its_day_and_end_after_it_starts(vault: Vault, api: TestClient, t0: str, t1: str) -> None:
    end = t1 if "T" in t1 else f"{DAY}T{t1}-04:00"
    response = api.request("DELETE", f"/day/{DAY}/hours", json={"t0": f"{DAY}T{t0}-04:00", "t1": end})
    assert response.status_code == 422
    assert not (vault.root / "deletions.jsonl").exists()


def test_deleting_needs_the_vault_token(vault: Vault, api: TestClient) -> None:
    del api.headers["Authorization"]
    assert delete(api, DAY, "09:00:00", "10:00:00").status_code == 401


def test_sensor_rows_in_the_span_go_from_a_csv_and_a_zip(tmp_path: Path) -> None:
    base = 1_792_605_600  # 2026-10-21T18:00:00Z
    text = "time,seconds_elapsed,latitude\n" + "".join(f"{(base + s) * 10**9},{s},43.6\n" for s in range(0, 600, 60))
    span = (instant("2026-10-21T18:02:00+00:00"), instant("2026-10-21T18:05:00+00:00"))
    cut, removed = _cut_csv(text, span)
    assert removed == 3 and [line.split(",")[1] for line in cut.splitlines()[1:]] == [
        "0",
        "60",
        "300",
        "360",
        "420",
        "480",
        "540",
    ]
    import zipfile

    path = tmp_path / "walk.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Location.csv", text)
        archive.writestr("Metadata.csv", "device,version\nphone,1\n")
    assert _cut_zip(path, span) == 3
    with zipfile.ZipFile(path) as archive:
        assert (
            archive.read("Location.csv").decode() == cut
            and archive.read("Metadata.csv") == b"device,version\nphone,1\n"
        )


def test_the_last_hour_is_deleted_a_day_at_a_time(vault: Vault, api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as dt

    import ingest.api

    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:
            return dt.datetime(2026, 10, 23, 0, 20, tzinfo=tz)

    monkeypatch.setattr(ingest.api, "datetime", Clock)
    response = api.request("DELETE", "/recent", params={"minutes": 60})
    assert response.status_code == 200, response.text
    spans = [(d["date"], d["t0"][11:16], d["t1"][11:16]) for d in response.json()["deleted"]]
    assert spans == [("2026-10-22", "23:20", "00:00"), ("2026-10-23", "00:00", "00:20")]

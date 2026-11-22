"""What I saw (GET /day/{date}/seen): the capture morning page by page, in the order the pages came
to the front, with what was read, stared at, copied and searched on each."""

from conftest import CAPTURE_DAY, capture_session
from fastapi.testclient import TestClient
from ingest.seen import _words
from ingest.vault import Vault


def test_the_morning_page_by_page(vault: Vault, client: TestClient) -> None:
    capture_session(vault)
    seen = client.get(f"/day/{CAPTURE_DAY}/seen").json()
    titles = [p["title"] for p in seen["pages"]]
    assert titles == [
        "CS 136 Assignment 3 - Hash tables",
        "linear probing vs chaining - Google Search",
        "Hash table - Wikipedia",
        "Hash Tables - Computerphile - YouTube",
    ]
    course, google, wiki, video = seen["pages"]
    assert course["site"] == "learn.uwaterloo.ca" and course["seconds"] == 300.0
    assert [i["kind"] for i in course["items"]] == ["look", "stay", "read"]
    read = course["items"][2]
    assert read["value"] == 1.0 and read["text"].startswith("Open addressing stores every key")
    assert [(i["kind"], i["text"]) for i in google["items"]] == [
        ("search", "linear probing vs chaining"),
        ("click", "Clicked “Hash table - Wikipedia” to en.wikipedia.org"),
    ]
    assert len(wiki["spans"]) == 2 and wiki["seconds"] == 699.0  # 09:05:21-09:12 and 09:20-09:25
    assert wiki["items"] == [
        {"t": f"{CAPTURE_DAY}T09:10:00-04:00", "kind": "copy", "text": "“Linear probing”", "value": None}
    ]
    assert (video["items"][0]["kind"], video["items"][0]["text"]) == ("media", "Played media for 5 min")
    assert seen["totals"] == {"seconds": 1380.0, "pages": 4, "read": 1}


def test_a_day_with_no_records_is_not_found(client: TestClient) -> None:
    assert client.get("/day/2026-01-01/seen").status_code == 404


def test_a_title_with_a_colon_keeps_the_words_whole() -> None:
    text = "Read in Assignment 3: Hash tables (learn.uwaterloo.ca): Open addressing: probes"
    assert _words("read", text, "Assignment 3: Hash tables") == "Open addressing: probes"


def test_an_app_whose_windows_are_not_kept_is_listed_as_itself(vault: Vault, client: TestClient) -> None:
    capture_session(vault)
    log = vault.root / "capture" / CAPTURE_DAY / "windows.jsonl"
    vault.append_lines(log, [
        {"id": "w-vault", "t": f"{CAPTURE_DAY}T09:40:00-04:00", "kind": "window", "app": "keepass.exe", "private": True},
        {"id": "w-code", "t": f"{CAPTURE_DAY}T09:42:00-04:00", "kind": "window", "app": "code.exe", "title": "notes.md"},
        {"id": "w-end", "t": f"{CAPTURE_DAY}T09:43:00-04:00", "kind": "away", "since": f"{CAPTURE_DAY}T09:43:00-04:00"},
    ])  # fmt: skip
    seen = client.get(f"/day/{CAPTURE_DAY}/seen")
    assert seen.status_code == 200  # it answered 500: the app has no page, only itself
    app = next(p for p in seen.json()["pages"] if "keepass" in p["key"])
    assert (app["title"], app["seconds"], app["items"]) == ("keepass", 120.0, [])


def test_a_day_told_only_by_its_events_has_no_pages(client: TestClient) -> None:
    seen = client.get("/day/2026-10-20/seen")  # the sample day: "Planned the day" is an event, not a page
    assert seen.status_code == 200 and seen.json()["pages"] == []

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

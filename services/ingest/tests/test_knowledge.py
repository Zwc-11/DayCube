"""A day's knowledge base as the viewer reads it: days with only computer capture, the day's
notes with links and backlinks, one note, and search as you type."""

from conftest import CAPTURE_DAY, DAY, capture_session
from fastapi.testclient import TestClient
from ingest.api import create_app
from ingest.config import Settings
from ingest.vault import Vault


def client_for(vault: Vault) -> TestClient:
    return TestClient(create_app(Settings(vault_root=vault.root, vector_search=False, _env_file=None)))


def test_days_with_only_computer_capture_are_listed(vault: Vault) -> None:
    capture_session(vault)
    with client_for(vault) as client:
        days = {d["date"]: d for d in client.get("/days").json()}
        assert list(days) == [CAPTURE_DAY, DAY]  # newest first
        assert days[CAPTURE_DAY]["capture"] is True and days[CAPTURE_DAY]["kinds"] == []
        assert days[CAPTURE_DAY]["stats"]["places"] == 0
        assert days[DAY]["capture"] is False and days[DAY]["kinds"]
        client.get(f"/day/{CAPTURE_DAY}/notes")  # indexes the day
        days = {d["date"]: d for d in client.get("/days").json()}
        assert days[CAPTURE_DAY]["records"] > 0


def test_a_day_opens_as_its_notes_with_links_backlinks_and_graph(vault: Vault) -> None:
    capture_session(vault)
    own = vault.root / "notes" / "Ideas" / "Plan.md"
    own.parent.mkdir(parents=True)
    own.write_text("Revisit [[Daily/2026-10-22]] and [[Courses/CS 136|the course]]; also [[Not yet]].\n")
    with client_for(vault) as client:
        day = client.get(f"/day/{CAPTURE_DAY}/notes").json()  # writes the notes on first read
        assert day["daily"] == "Daily/2026-10-22.md" and day["places"] is False
        listed = {n["path"]: (n["kind"], n["role"]) for n in day["notes"]}
        assert listed["Daily/2026-10-22.md"] == ("daily", "daily")
        assert listed["Sources/Hash table - Wikipedia.md"] == ("source", "linked")
        assert listed["Courses/CS 136.md"] == ("course", "linked")
        assert listed["Ideas/Plan.md"] == ("note", "backlink")
        later = vault.root / "notes" / "Later.md"
        later.write_text("Back to [[Daily/2026-10-22]].\n")  # written after the day was opened
        again = {n["path"] for n in client.get(f"/day/{CAPTURE_DAY}/notes").json()["notes"]}
        assert "Later.md" in again
        assert day["kinds"]["web.read"] == 1 and day["records"] == sum(day["kinds"].values())
        course = next(n for n in day["graph"]["nodes"] if n["kind"] == "course")
        assert course["note"] == "Courses/CS 136.md"

        note = client.get("/note", params={"path": "Courses/CS 136.md"}).json()
        assert note["kind"] == "course" and "## Due" in note["markdown"]
        assert {b["path"] for b in note["backlinks"]} >= {"Ideas/Plan.md", "Daily/2026-10-22.md"}
        plan = client.get("/note", params={"path": "Ideas/Plan.md"}).json()
        targets = {link["target"]: link["path"] for link in plan["links"]}
        assert targets == {
            "Daily/2026-10-22": "Daily/2026-10-22.md",
            "Courses/CS 136": "Courses/CS 136.md",
            "Not yet": None,
        }
        for bad in ("../.token", "/etc/passwd", ".obsidian/app.md", "Daily/../../.token", "Daily\\x.md", "notes.txt"):
            assert client.get("/note", params={"path": bad}).status_code == 400, bad
        assert client.get("/note", params={"path": "Missing.md"}).status_code == 404
        assert client.get("/day/2026-01-01/notes").status_code == 404


def test_new_capture_refreshes_the_days_notes_on_the_next_read(vault: Vault) -> None:
    import json

    capture_session(vault)
    with client_for(vault) as client:
        client.get(f"/day/{CAPTURE_DAY}/notes")
        with (vault.root / "capture" / CAPTURE_DAY / "browser.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"id": "s9-0000001", "t": f"{CAPTURE_DAY}T10:00:00-04:00", "kind": "search",
                                     "tab": 3, "window": 1, "url": "https://www.google.com/search?q=cuckoo+hashing",
                                     "title": "cuckoo hashing - Google Search", "engine": "google",
                                     "query": "cuckoo hashing"}) + "\n")  # fmt: skip
        client.get(f"/day/{CAPTURE_DAY}/notes")
        daily = client.get("/note", params={"path": "Daily/2026-10-22.md"}).json()["markdown"]
        assert "“cuckoo hashing”" in daily


def test_search_as_you_type_points_each_result_at_its_note(vault: Vault) -> None:
    capture_session(vault)
    with client_for(vault) as client:
        client.get(f"/day/{CAPTURE_DAY}/notes")
        found = client.get("/search", params={"q": "open addressing", "date": CAPTURE_DAY}).json()
        best = found["evidence"][0]
        assert best["type"] == "web.read" and best["note"] == "Sources/CS 136 Assignment 3 - Hash tables.md"
        assert best["match"].startswith("Open addressing stores every key")
        assert found["took_ms"] >= 0 and found["sentences"]
        everywhere = client.get("/search", params={"q": "what did I search"}).json()  # no token needed
        assert everywhere["evidence"][0]["type"] == "web.search"
        assert everywhere["evidence"][0]["note"] == "Daily/2026-10-22.md"  # a search has no page note
        assert client.get("/search", params={"q": ""}).status_code == 422

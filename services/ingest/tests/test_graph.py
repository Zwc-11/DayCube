"""The knowledge graph: entities the records name exactly, and how they met."""

from datetime import date

from conftest import CAPTURE_DAY, DAY, capture_session
from ingest.graph import canonical_url, courses, deadlines
from ingest.store import LifeStore
from ingest.vault import Vault


def test_course_codes_and_due_dates_are_read_exactly() -> None:
    assert courses("CS 136 Assignment 3, MATH135 and AFM 101 (PDF 100 is not one)") == ["CS 136", "MATH 135", "AFM 101"]
    assert courses("PM 11 and ISO 8601") == []
    seen = date(2026, 10, 22)
    assert deadlines("Due Friday October 30 at 11:59 PM. Submit on Marmoset.", seen) == ["2026-10-30T23:59"]
    assert deadlines("Quiz 2 due Oct. 3", seen) == ["2027-10-03"]  # already past this year: next year
    assert deadlines("due Dec 4th, 2026 at 9am", seen) == ["2026-12-04T09:00"]
    assert deadlines("nothing due here", seen) == [] and deadlines("due February 30", seen) == []


def test_urls_are_known_without_fragments_or_tracking() -> None:
    assert canonical_url("https://WWW.Example.com/a/b/?utm_source=x&id=7#top") == "https://www.example.com/a/b?id=7"
    assert canonical_url("https://example.com") == "https://example.com/"


def test_the_capture_morning_becomes_a_graph(vault: Vault) -> None:
    capture_session(vault)
    store = LifeStore(vault)
    graph = store.graph()
    nodes = {(n["kind"], n["name"]): n for n in graph["nodes"]}
    names = {n["id"]: (n["kind"], n["name"]) for n in graph["nodes"]}
    edges = {(names[e["source"]], names[e["target"]], e["kind"]) for e in graph["edges"]}
    course = ("course", "CS 136")
    learn = ("page", "CS 136 Assignment 3 - Hash tables")
    wiki = ("page", "Hash table - Wikipedia")
    query = ("query", "linear probing vs chaining")
    deadline = ("deadline", "CS 136 due 2026-10-30T23:59")
    assert {course, learn, wiki, query, deadline, ("site", "learn.uwaterloo.ca"), ("site", "en.wikipedia.org")} <= set(
        nodes
    )
    assert (learn, ("site", "learn.uwaterloo.ca"), "part_of") in edges
    assert (learn, course, "mentions") in edges
    assert (query, wiki, "led_to") in edges  # the search's result opened
    assert (course, deadline, "due") in edges  # from the stare at the due date
    assert nodes[learn]["mentions"] == 4  # visit, read, dwell, look on that page
    detail = store.entity(nodes[deadline]["id"])
    assert detail["mentions"][0]["type"] == "web.dwell" and "October 30" in detail["mentions"][0]["text"]
    assert {(n["kind"], n["name"], n["link"], n["direction"]) for n in detail["neighbours"]} == {
        ("course", "CS 136", "due", "in")
    }


def test_a_day_rewritten_replaces_its_mentions_and_drops_orphans(vault: Vault) -> None:
    capture_session(vault)
    store = LifeStore(vault)
    store.ensure()
    path = vault.root / "capture" / CAPTURE_DAY / "browser.jsonl"
    path.write_text(
        "\n".join(line for line in path.read_text().splitlines() if "youtube" not in line) + "\n", encoding="utf-8"
    )
    kinds = {(n["kind"], n["name"]) for n in store.graph()["nodes"]}
    assert ("site", "youtube.com") not in kinds and ("site", "en.wikipedia.org") in kinds
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(vault.root / "daycube.sqlite")) as db:
        orphans = db.execute(
            "SELECT count(*) FROM entities e WHERE NOT EXISTS (SELECT 1 FROM mentions WHERE entity_id = e.id) "
            "AND NOT EXISTS (SELECT 1 FROM links WHERE src = e.id OR dst = e.id)"
        ).fetchone()[0]
    assert orphans == 0


def test_vault_days_add_places_people_and_topics(vault: Vault) -> None:
    store = LifeStore(vault)
    store.update(DAY)
    kinds = {n["kind"] for n in store.graph()["nodes"]}
    assert "place" in kinds
    window = store.graph(kinds=["place"])
    assert window["nodes"] and all(n["kind"] == "place" for n in window["nodes"])


def test_the_api_serves_the_graph_and_an_entity(vault: Vault) -> None:
    from fastapi.testclient import TestClient
    from ingest.api import create_app
    from ingest.config import Settings

    capture_session(vault)
    with TestClient(create_app(Settings(vault_root=vault.root, vector_search=False, _env_file=None))) as client:
        whole = client.get("/graph").json()
        assert any(n["name"] == "CS 136" for n in whole["nodes"])
        assert client.get("/graph", params={"since": "2026-10-23"}).json()["nodes"] == []  # after the morning
        courses = client.get("/graph", params={"kinds": "course,deadline"}).json()
        assert {n["kind"] for n in courses["nodes"]} == {"course", "deadline"} and courses["edges"]
        course = next(n for n in courses["nodes"] if n["kind"] == "course")
        detail = client.get(f"/entity/{course['id']}").json()
        assert detail["name"] == "CS 136" and detail["mention_count"] >= 1
        assert client.get("/entity/999999").status_code == 404

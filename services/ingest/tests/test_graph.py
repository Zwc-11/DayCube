"""The knowledge graph: entities the records name exactly, and how they met."""

from datetime import date

import pytest
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


def test_search_navigation_join_keeps_tab_time_cutoff_and_stable_ties() -> None:
    from ingest.capture.synthesize import CaptureDay
    from ingest.graph import extract

    capture = CaptureDay(CAPTURE_DAY)
    capture.searches = [
        {"id": "a", "tab": 1, "t": "2026-10-22T10:00:00-04:00", "query": "tables"},
        {"id": "b", "tab": 2, "t": "2026-10-22T14:00:00+00:00", "query": "late"},
        {"id": "c", "tab": 3, "t": "2026-10-22T14:00:00+00:00", "query": "absent"},
    ]
    capture.pages = [
        {"id": "end", "tab": 1, "t": "2026-10-22T14:05:00+00:00", "url": "https://example.org/first"},
        {"id": "tie", "tab": 1, "t": "2026-10-22T14:05:00+00:00", "url": "https://example.org/tied"},
        {"id": "same", "tab": 1, "t": "2026-10-22T14:00:00+00:00", "url": "https://example.org/same-time"},
        {"id": "wrong-tab", "tab": 4, "t": "2026-10-22T14:00:01+00:00", "url": "https://example.org/wrong-tab"},
        {"id": "results", "tab": 1, "t": "2026-10-22T14:00:01+00:00", "url": "https://www.google.com/search?q=tables"},
        {"id": "late", "tab": 2, "t": "2026-10-22T14:05:01+00:00", "url": "https://example.org/late"},
    ]
    units = [{"evidence_id": f"capture:search:{s['id']}", "type": "web.search"} for s in capture.searches]
    graph = extract(CAPTURE_DAY, units, capture)
    links = [(a[1], b[1], evidence) for a, b, kind, evidence in graph.links if kind == "led_to"]
    assert links == [("tables", "https://example.org/first", "capture:search:a")]


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
    deadline = ("deadline", "CS 136 due Fri Oct 30, 11:59 PM")
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


def test_a_day_rewritten_replaces_its_mentions_and_drops_orphans(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("ingest.store.db.monotonic", lambda: clock[0])
    capture_session(vault)
    store = LifeStore(vault)
    store.ensure()
    path = vault.root / "capture" / CAPTURE_DAY / "browser.jsonl"
    path.write_text(
        "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if "youtube" not in line) + "\n",
        encoding="utf-8",
    )
    clock[0] = 2.01
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
    # Places carry the day's names, and each visit is linked from the place before it.
    names = {n["id"]: n["name"] for n in window["nodes"]}
    assert {"Home · morning", "Maple Café", "Engineering Hall"} <= set(names.values())
    trail = {(names[e["source"]], names[e["target"]]) for e in window["edges"] if e["kind"] == "led_to"}
    assert ("Home · morning", "Maple Café") in trail and ("Maple Café", "Engineering Hall") in trail
    vault.write_json(vault.root / "places.json", {"cafe1": "The usual café"})  # a rename shows at once
    assert "The usual café" in {n["name"] for n in store.graph(kinds=["place"])["nodes"]}


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


def test_the_data_center_lists_sources_and_the_database(vault: Vault) -> None:
    from fastapi.testclient import TestClient
    from ingest.api import create_app
    from ingest.config import Settings
    from ingest.notes import Notes

    capture_session(vault)
    Notes(LifeStore(vault)).write_day(CAPTURE_DAY)
    with TestClient(create_app(Settings(vault_root=vault.root, vector_search=False, _env_file=None))) as client:
        data = client.get("/data").json()
    sources = {s["name"]: s for s in data["sources"]}
    assert (
        sources["Computer capture: reading.jsonl"]["files"] == 1
        and sources["Computer capture: reading.jsonl"]["days"] == 1
    )
    assert sources["Notes"]["files"] == 5 and sources["Vault days"]["days"] == 1  # the seeded sample day
    db = data["database"]
    assert db["kind"] == "derived" and db["rows"]["units"] > 0 and db["units_by_type"]["web.read"] == 1
    assert db["entities_by_kind"]["course"] == 1 and db["links_by_kind"]["led_to"] >= 1
    assert db["rows"]["notes"] == 5 and data["capture_days"] == [CAPTURE_DAY]
    assert data["totals"]["source_bytes"] > 0 and data["totals"]["derived_bytes"] > 0


def test_a_change_in_what_is_derived_reads_every_day_and_note_again(vault: Vault) -> None:
    import ingest.store.db as db_module

    capture_session(vault)
    store = LifeStore(vault)
    store.ensure()
    with store.connect() as db:
        db.execute("UPDATE entities SET name = 'stale' WHERE kind = 'deadline'")
        db.execute("UPDATE meta SET value = '1' WHERE key = 'derived'")
    fresh = LifeStore(vault)  # a new build of the code opens the same file
    assert any(n["name"] == "CS 136 due Fri Oct 30, 11:59 PM" for n in fresh.graph(kinds=["deadline"])["nodes"])
    with fresh.connect() as db:
        assert db.execute("SELECT value FROM meta WHERE key = 'derived'").fetchone()[0] == db_module.DERIVED_VERSION


def test_a_database_from_before_note_links_gains_them_in_place(vault: Vault) -> None:
    import sqlite3

    capture_session(vault)
    store = LifeStore(vault)
    store.ensure()
    with sqlite3.connect(store.path) as db:  # as built by schema 4 before note links, or their first form
        db.execute("DROP TABLE note_links")
        db.execute("CREATE TABLE note_links (path TEXT NOT NULL, target TEXT NOT NULL, label TEXT, resolved TEXT)")
        db.execute("DROP INDEX mentions_unit")
        db.execute("UPDATE meta SET value = '2' WHERE key = 'derived'")
    units = store.stats()["units"]
    fresh = LifeStore(vault)
    with fresh.connect() as db:
        assert "name" in {row[1] for row in db.execute("PRAGMA table_info(note_links)")}
        assert "mentions_unit" in {row[1] for row in db.execute("PRAGMA index_list(mentions)")}
    assert fresh.stats()["units"] == units  # nothing rebuilt

"""Linked notes: an Obsidian folder written from the vault that keeps the person's own writing,
and read back so that writing is searchable and its [[links]] join the graph."""

from conftest import CAPTURE_DAY, capture_session
from ingest.ask.retrieve import retrieve
from ingest.notes import Notes, chunks, front_matter, links, merge, user_text
from ingest.notes.markdown import END, START, file_name
from ingest.store import LifeStore
from ingest.vault import Vault


def test_markdown_basics() -> None:
    text = "---\ndaycube: page https://x.org/a\ntags: [source]\n---\n\nMine [[Other note]] and [[Sources/A|alias]]."
    assert front_matter(text) == {"daycube": "page https://x.org/a", "tags": "[source]"}
    assert links(text + " ![[embed.png]] [[Note#Heading|h]]") == [
        ("Other note", None), ("Sources/A", "alias"), ("Note", "h"),
    ]  # fmt: skip
    note = merge(None, "generated one", {"daycube": "day 2026-10-22"})
    assert note.startswith("---\ndaycube: day 2026-10-22\n---\n") and START in note and END in note
    edited = note.replace(END, END + "\n\nMy own thoughts.")
    again = merge(edited, "generated two", {})
    assert "generated two" in again and "generated one" not in again and "My own thoughts." in again
    assert user_text(again) == "My own thoughts."
    assert merge("Someone's note", "region", {}).startswith("Someone's note\n\n" + START)
    assert file_name('Q: what is "a/b"? <x>') == "Q what is a b x"
    assert [c.heading for c in chunks("# Plan\n\nfirst\n\n## Ideas\n\nsecond")] == ["Plan", "Ideas"]


def test_the_capture_morning_writes_linked_notes(vault: Vault) -> None:
    capture_session(vault)
    notes = Notes(LifeStore(vault))
    written = {p.relative_to(notes.root).as_posix() for p in notes.write_day(CAPTURE_DAY)}
    assert written == {
        "Daily/2026-10-22.md",
        "Sources/CS 136 Assignment 3 - Hash tables.md",
        "Sources/Hash table - Wikipedia.md",
        "Sources/Hash Tables - Computerphile - YouTube.md",  # 6 min on screen
        "Courses/CS 136.md",
    }  # the Google results page (21 s, no attention) gets no note
    daily = (notes.root / "Daily/2026-10-22.md").read_text()
    assert "- [[Courses/CS 136|CS 136]] due 2026-10-30 23:59 (seen 09:00)" in daily
    assert "“linear probing vs chaining” → [[Sources/Hash table - Wikipedia|Hash table - Wikipedia]]" in daily
    assert "(100% of the lines)" in daily and "(16 s)" in daily
    course = (notes.root / "Courses/CS 136.md").read_text()
    assert "- 2026-10-30 23:59 (seen 2026-10-22)" in course
    assert "[[Sources/CS 136 Assignment 3 - Hash tables|CS 136 Assignment 3 - Hash tables]]" in course


def test_regenerating_keeps_edits_and_renamed_notes(vault: Vault) -> None:
    capture_session(vault)
    store = LifeStore(vault)
    notes = Notes(store)
    notes.write_day(CAPTURE_DAY)
    source = notes.root / "Sources/Hash table - Wikipedia.md"
    renamed = notes.root / "Sources/Hashing (my notes).md"
    source.rename(renamed)
    renamed.write_text(renamed.read_text() + "\nLinear probing clusters; see [[Courses/CS 136]].\n")
    Notes(store).write_day(CAPTURE_DAY)
    assert not source.exists()  # found by its front matter, not its name
    text = renamed.read_text()
    assert "Linear probing clusters" in text and text.count(START) == 1
    daily = (notes.root / "Daily/2026-10-22.md").read_text()
    assert "[[Sources/Hashing (my notes)|Hash table - Wikipedia]]" in daily


def test_own_notes_are_searched_and_linked(vault: Vault) -> None:
    capture_session(vault)
    store = LifeStore(vault)
    Notes(store).write_day(CAPTURE_DAY)
    own = store.notes_root / "Ideas" / "Cuckoo hashing.md"
    own.parent.mkdir(parents=True)
    own.write_text("# Idea\n\nTry cuckoo hashing for the assignment; compare with [[Sources/Hash table - Wikipedia]] "
                   "and [[A note not written yet]].\n")  # fmt: skip
    found = retrieve(store, "cuckoo hashing")
    assert found and found[0]["type"] == "note.text" and found[0]["source_ref"] == "notes/Ideas/Cuckoo hashing.md"
    assert found[0]["text"].startswith("Cuckoo hashing — Idea: Try cuckoo hashing")
    graph = store.graph()
    names = {n["id"]: (n["kind"], n["name"]) for n in graph["nodes"]}
    edges = {(names.get(e["source"]), names.get(e["target"]), e["kind"]) for e in graph["edges"]}
    idea = ("note", "Cuckoo hashing")
    assert (idea, ("page", "Hash table - Wikipedia"), "links") in edges  # the generated note is the page
    assert (idea, ("note", "A note not written yet"), "links") in edges  # unresolved, as in Obsidian
    # Only the person's own links: generated regions restate what the graph already holds.
    assert {source for source, _, kind in edges if kind == "links"} == {idea}
    # Generated regions are not indexed twice: the daily note's text is not a note hit.
    assert all(r["type"] != "note.text" for r in retrieve(store, "linear probing vs chaining"))
    own.unlink()
    assert retrieve(store, "cuckoo hashing") == []


def test_notes_through_the_api(vault: Vault) -> None:
    from fastapi.testclient import TestClient
    from ingest.api import create_app
    from ingest.config import Settings

    capture_session(vault)
    with TestClient(create_app(Settings(vault_root=vault.root, vector_search=False, _env_file=None))) as client:
        assert client.post(f"/notes/{CAPTURE_DAY}").status_code == 401
        auth = {"Authorization": f"Bearer {vault.token}"}
        written = client.post(f"/notes/{CAPTURE_DAY}", headers=auth).json()["notes"]
        assert "Daily/2026-10-22.md" in written
        listed = {n["path"]: n["identity"] for n in client.get("/notes").json()}
        assert listed["Courses/CS 136.md"] == "course CS 136"
        assert client.post("/notes/2026-01-01", headers=auth).status_code == 404

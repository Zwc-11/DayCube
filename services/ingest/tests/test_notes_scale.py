"""Note reads and page metadata stay scoped as unrelated notes and pages accumulate."""

from conftest import CAPTURE_DAY, capture_session
from ingest.notes.read import DayNotes
from ingest.notes.write import Notes
from ingest.store import LifeStore
from ingest.vault import Vault


def test_page_metadata_batches_keep_duration_and_attention_rules(vault: Vault) -> None:
    capture_session(vault)
    store = LifeStore(vault)
    store.ensure()
    with store.connect() as db:
        db.executemany(
            "INSERT INTO entities(kind, key, name) VALUES ('page', ?, ?)",
            [(f"https://synthetic.invalid/{i}", f"Unused {i}") for i in range(510)],
        )
        ids = {r[0] for r in db.execute("SELECT id FROM entities WHERE kind = 'page'")} | {-1}
        statements: list[str] = []
        db.set_trace_callback(statements.append)
        pages = Notes(store)._pages(db, ids)
        db.set_trace_callback(None)
        assert len(statements) == 6  # two batches, independent of page count within each batch
        assert -1 not in pages
        by_title = {p["title"]: p for p in pages.values()}
        assert by_title["Hash table - Wikipedia"]["noteworthy"] is True  # attended and reached by search
        assert round(by_title["Hash table - Wikipedia"]["seconds"]) == 699
        assert by_title["Hash Tables - Computerphile - YouTube"]["noteworthy"] is True  # six-minute visit
        assert by_title["linear probing vs chaining - Google Search"]["noteworthy"] is False
        assert by_title["Unused 0"]["seconds"] == 0 and not by_title["Unused 0"]["noteworthy"]
        assert Notes(store)._pages(db, set()) == {}


def test_unrelated_notes_do_not_change_day_view_and_identity_ties_stay_stable(vault: Vault) -> None:
    capture_session(vault)
    store = LifeStore(vault)
    reader = DayNotes(store)
    before = reader.view(CAPTURE_DAY)
    own = store.notes_root / "Ideas"
    own.mkdir()
    for i in range(520):
        (own / f"Unrelated {i}.md").write_text("An unrelated note.\n", encoding="utf-8")
    assert reader.view(CAPTURE_DAY) == before
    alias = own / "Wiki alias.md"
    alias.write_text("---\ndaycube: page https://en.wikipedia.org/wiki/Hash_table\n---\n", encoding="utf-8")
    view = reader.view(CAPTURE_DAY)
    page = next(n for n in view["graph"]["nodes"] if n["kind"] == "page" and n["name"] == "Hash table - Wikipedia")
    assert page["note"] == "Ideas/Wiki alias.md"  # previous row-scan order: last identity wins
    alias.unlink()
    view = reader.view(CAPTURE_DAY)
    page = next(n for n in view["graph"]["nodes"] if n["kind"] == "page" and n["name"] == "Hash table - Wikipedia")
    assert page["note"] == "Sources/Hash table - Wikipedia.md"

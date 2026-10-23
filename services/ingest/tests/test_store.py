"""The vault database: one file for every day, kept equal to a rebuild from the day files."""

import copy
import shutil
import sqlite3
from contextlib import closing

import pytest
from conftest import DAY, WordEmbedder, clone_day
from ingest.ask.retrieve import retrieve
from ingest.index.build import UNIT_COLUMNS
from ingest.store import LifeStore
from ingest.store.vectors import Block, VectorIndex
from ingest.time import instant
from ingest.vault import Vault

OTHER = "2026-10-21"


def test_retrieval_prepares_each_day_once(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    clone_day(vault, DAY, OTHER)
    store = LifeStore(vault)
    expected = retrieve(store, "hinge inference loop")
    calls: list[str] = []
    snapshot = vault.snapshot

    def counted(day: str) -> object:
        calls.append(day)
        return snapshot(day)

    monkeypatch.setattr(vault, "snapshot", counted)
    assert retrieve(store, "hinge inference loop") == expected
    assert len(expected) > 2 and sorted(calls) == [DAY, OTHER]


def test_vector_scope_loads_only_requested_days(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    clone_day(vault, DAY, OTHER)
    store = LifeStore(vault, WordEmbedder())
    store.ensure()
    loaded: list[str] = []
    load = Block.load

    def counted(cls: type[Block], db: sqlite3.Connection, day: str) -> Block:
        loaded.append(day)
        return load(db, day)

    monkeypatch.setattr(Block, "load", classmethod(counted))
    first = store.nearest("citation panel", day=DAY, k=3)
    assert first and store.nearest("citation panel", day=DAY, k=3) == first
    assert loaded == [DAY]
    assert store.nearest("citation panel", day=OTHER, k=3)
    assert loaded == [DAY, OTHER]


@pytest.mark.parametrize("budget", [1, 2048])
def test_vector_cache_eviction_preserves_results(vault: Vault, budget: int) -> None:
    clone_day(vault, DAY, OTHER)
    embedder = WordEmbedder()
    store = LifeStore(vault, embedder)
    store.ensure()
    index = VectorIndex(cache_bytes=budget)
    query = embedder.query("citation panel")
    with store.connect() as db:
        for day in [DAY, OTHER, DAY, OTHER]:
            expected = VectorIndex().search(db, query, 3, days=[day])
            assert index.search(db, query, 3, days=(d for d in [day])) == expected
            assert index.cache_size_bytes <= budget
        index.clear()
        assert index.cache_size_bytes == 0
        assert index.search(db, query, 3, days=[DAY]) == VectorIndex().search(db, query, 3, days=[DAY])
        assert index.search(db, query, 0) == []
        with pytest.raises(ValueError, match="nonnegative"):
            index.search(db, query, -1)


def test_vector_cache_never_returns_a_deleted_day(vector_store: LifeStore) -> None:
    vector_store.ensure([DAY])
    assert vector_store.nearest("citation panel", day=DAY, k=3)
    assert vector_store.embedder is not None
    query = vector_store.embedder.query("citation panel")
    with vector_store.connect() as db:
        vector_store._delete_day(db, DAY)
        assert vector_store.index.search(db, query, 3) == []
        assert vector_store.index.search(db, query, 3, days=[DAY]) == []


def snapshot(store: LifeStore) -> list[tuple]:
    """Everything a rebuild must reproduce, without row ids (they are never reused)."""
    with closing(sqlite3.connect(store.path)) as db:
        db.row_factory = sqlite3.Row
        units = [tuple(row[c] for c in (*UNIT_COLUMNS, "ts", "text_hash")) for row in db.execute("SELECT * FROM units")]
        days = [tuple(row) for row in db.execute("SELECT day, tz, stamp, units, model, unembedded FROM days")]
    return sorted(units, key=repr) + sorted(days)


def test_incremental_updates_equal_a_rebuild_from_files(vault: Vault, tmp_path: object) -> None:
    store = LifeStore(vault)
    store.ensure()
    clone_day(vault, DAY, OTHER)
    vault.append_events(DAY, [dict(copy.deepcopy(vault.events(DAY)[0]), id="ev_added", label="Late addition")])
    vault.write_json(vault.root / "places.json", {"cafe1": "Zephyr Hut"})
    store.ensure()
    rebuilt = LifeStore(Vault(vault.root), path=vault.root / "rebuilt.sqlite")
    rebuilt.ensure()
    assert snapshot(store) == snapshot(rebuilt)
    assert store.stats()["days"] == 2


def test_searches_span_days_and_a_day_scope_stays_inside_it(vault: Vault) -> None:
    clone_day(vault, DAY, OTHER)
    store = LifeStore(vault)
    everywhere = retrieve(store, "hinge inference loop")
    assert {hit["day"] for hit in everywhere} == {DAY, OTHER}
    assert {hit["day"] for hit in retrieve(store, "hinge inference loop", day=OTHER)} == {OTHER}


def test_time_window_filters_both_methods(vault: Vault, embedder: WordEmbedder) -> None:
    store = LifeStore(vault, embedder)
    noon = instant(f"{DAY}T12:00:00-04:00").timestamp()
    hits = retrieve(store, "Maple Café", day=DAY, since=noon)
    assert hits and all(instant(hit["iso_time"]).timestamp() >= noon for hit in hits)
    assert {hit["place"] for hit in hits} == {"cafe2"}


def test_a_deleted_day_leaves_the_database(vault: Vault) -> None:
    clone_day(vault, DAY, OTHER)
    store = LifeStore(vault)
    store.ensure()
    shutil.rmtree(vault.day_path(OTHER))
    store.ensure()
    assert store.stats()["days"] == 1
    assert {hit["day"] for hit in retrieve(store, "hinge")} == {DAY}


def test_a_deleted_database_is_rebuilt_on_the_next_search(vault: Vault) -> None:
    store = LifeStore(vault)
    first = retrieve(store, "demo order", day=DAY)
    for part in vault.root.glob("daycube.sqlite*"):
        part.unlink()
    assert retrieve(store, "demo order", day=DAY) == first


# -- meaning search ------------------------------------------------------------------------------


def test_meaning_search_finds_a_paraphrase_keywords_miss(vector_store: LifeStore) -> None:
    question = "Who is presenting the citation panel?"
    assert retrieve(LifeStore(vector_store.vault), question, day=DAY) == []
    hits = retrieve(vector_store, question, day=DAY)
    assert hits[0]["text"] == "Sam will show the evidence card after the question."
    assert hits[0]["matched"] == ["meaning"]


def test_unchanged_text_is_never_embedded_twice(vault: Vault, embedder: WordEmbedder) -> None:
    store = LifeStore(vault, embedder)
    store.ensure()
    first = len(embedder.embedded)
    assert first == len(set(embedder.embedded))  # repeated text inside a day is embedded once
    vault.append_events(DAY, [dict(copy.deepcopy(vault.events(DAY)[0]), id="ev_new", label="A brand new note")])
    store.ensure()
    assert len(embedder.embedded) == first + 1 and embedder.embedded[-1].startswith("A brand new note")
    clone_day(vault, DAY, OTHER)  # same texts on another day reuse that day's vectors
    store.ensure()
    assert len(embedder.embedded) == first + 1
    assert store.stats()["unembedded"] == 0


def test_a_day_too_big_to_embed_inline_waits_for_the_index_command(vault: Vault, monkeypatch: object) -> None:
    import ingest.store.db as store_db

    monkeypatch.setattr(store_db, "INLINE_EMBED_LIMIT", 3)
    embedder = WordEmbedder()
    store = LifeStore(vault, embedder)
    store.ensure()
    assert embedder.embedded == [] and store.stats()["unembedded"] == 28
    assert retrieve(store, "hinge", day=DAY)  # keywords still answer
    store.ensure()
    assert embedder.embedded == []  # not retried on every query
    store.update(DAY)
    assert store.stats()["unembedded"] == 0


def test_a_new_model_replaces_every_vector(vault: Vault) -> None:
    LifeStore(vault, WordEmbedder("test/words-v1")).ensure()
    replacement = WordEmbedder("test/words-v2", dim=32)
    store = LifeStore(Vault(vault.root), replacement)
    store.ensure()
    assert len(replacement.embedded) == len(set(replacement.embedded)) > 0
    assert store.stats() | {"bytes": 0} == {
        "days": 1,
        "units": 28,
        "unembedded": 0,
        "model": "test/words-v2",
        "bytes": 0,
    }


def test_a_day_rewritten_without_search_gets_its_vectors_back(vault: Vault) -> None:
    LifeStore(vault, WordEmbedder()).ensure()
    vault.append_events(DAY, [dict(copy.deepcopy(vault.events(DAY)[0]), id="ev_off", label="Written offline")])
    LifeStore(Vault(vault.root)).ensure()  # keyword-only: the day's rows, and so its vectors, are replaced
    store = LifeStore(Vault(vault.root), WordEmbedder())
    store.ensure()
    with store.connect() as db:
        vectors = {row[0] for row in db.execute("SELECT id FROM vectors")}
        units = {row[0] for row in db.execute("SELECT id FROM units")}
    assert vectors == units
    assert retrieve(store, "Written offline", day=DAY)[0]["id"] == "ev_off"


def test_search_blocks_follow_writes_by_another_process(vault: Vault, embedder: WordEmbedder) -> None:
    reader = LifeStore(vault, embedder)
    assert retrieve(reader, "Who is presenting the citation panel?", day=DAY)
    writer = LifeStore(Vault(vault.root), WordEmbedder())
    writer.vault.append_events(DAY, [dict(copy.deepcopy(vault.events(DAY)[0]), id="ev_far", label="Telescope dome")])
    writer.update(DAY)
    near = reader.nearest("Telescope dome", day=DAY, k=1)
    assert reader.rows([near[0][0]])[near[0][0]]["evidence_id"] == "ev_far"


def test_a_database_from_another_schema_version_is_rebuilt(vault: Vault) -> None:
    path = vault.root / "daycube.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        # The never-released first layout: row triggers keeping an FTS table in step.
        db.executescript(
            "CREATE TABLE units (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, text TEXT);"
            "CREATE VIRTUAL TABLE units_fts USING fts5(text, content='units', content_rowid='id');"
            "CREATE TRIGGER units_insert AFTER INSERT ON units BEGIN "
            "INSERT INTO units_fts(rowid, text) VALUES (new.id, new.text); END;"
            "INSERT INTO units(day, text) VALUES ('2026-10-20', 'stale');"
            "PRAGMA user_version = 1;"
        )
    store = LifeStore(vault)
    hits = retrieve(store, "hinge inference loop", day=DAY)
    assert hits and retrieve(store, "stale", day=DAY) == []
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO units_fts(units_fts, rank) VALUES ('integrity-check', 1)")
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2

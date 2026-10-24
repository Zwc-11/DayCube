"""A concurrent rewrite must not crash retrieval or return deleted evidence."""

import os
import sqlite3
from contextlib import closing

import pytest
from conftest import DAY, WordEmbedder
from ingest.ask.retrieve import retrieve
from ingest.store import LifeStore, vectors
from ingest.store.vectors import _rescore
from ingest.vault import Vault


def test_keyword_ids_deleted_before_rows_are_loaded(store: LifeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    store.ensure([DAY])
    monkeypatch.setattr(store, "rows", lambda ids: {})
    assert retrieve(store, "hinge inference", day=DAY) == []


def test_rescore_handles_candidates_deleted_by_another_writer() -> None:
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE vectors (id INTEGER PRIMARY KEY, embedding BLOB)")
        assert _rescore(db, bytes([1, 2]), [7], 1) == []


def test_vector_generation_and_rescore_share_one_snapshot(
    vector_store: LifeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = vector_store
    reader.ensure([DAY])
    before = reader.nearest("citation panel", day=DAY, k=3)
    assert before
    writer = LifeStore(Vault(reader.vault.root), WordEmbedder())
    rescore = vectors._rescore

    def replace_before_rescore(db: sqlite3.Connection, query: bytes, candidates: list[int], k: int) -> list:
        writer.update(DAY)  # commits new unit ids while the reader holds the old sign-bit block
        return rescore(db, query, candidates, k)

    monkeypatch.setattr(vectors, "_rescore", replace_before_rescore)
    assert reader.nearest("citation panel", day=DAY, k=3) == before
    monkeypatch.setattr(vectors, "_rescore", rescore)
    after = reader.nearest("citation panel", day=DAY, k=3)
    assert after and {i for i, _ in before}.isdisjoint(i for i, _ in after)


def test_note_rewrite_drops_stale_chunks_even_with_a_coarse_clock(vault: Vault) -> None:
    store = LifeStore(vault)
    store.notes_root.mkdir()
    path = store.notes_root / "Recipe.md"
    path.write_text("Cobalt recipe.\n", encoding="utf-8")
    store.sync_notes()
    ids = store.note_keyword('"Cobalt"', k=10)
    assert ids
    old = path.stat()
    path.write_text("A navy recipe with different ingredients.\n", encoding="utf-8")
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert store.note_rows(ids) == {}
    assert retrieve(store, "navy recipe")[0]["type"] == "note.text"

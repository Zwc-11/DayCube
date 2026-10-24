"""A concurrent rewrite must not crash retrieval or return deleted evidence."""

import os
import sqlite3
from contextlib import closing

import pytest
from conftest import DAY, WordEmbedder
from fastapi.testclient import TestClient
from ingest.api import create_app
from ingest.ask.query import Query
from ingest.ask.retrieve import retrieve
from ingest.config import Settings
from ingest.store import EmbeddingModelChanged, LifeStore, vectors
from ingest.store.vectors import _rescore
from ingest.vault import Vault


def test_keyword_ids_deleted_before_rows_are_loaded(store: LifeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    store.ensure([DAY])
    monkeypatch.setattr(store, "rows", lambda ids, **kwargs: {})
    assert retrieve(store, "hinge inference", day=DAY) == []


@pytest.mark.parametrize("boundary", ["keyword", "nearest", "listing"])
def test_retrieval_keeps_its_rows_when_another_writer_replaces_the_day(
    vector_store: LifeStore, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    reader = vector_store
    query = Query(text="", since=0) if boundary == "listing" else None
    expected = retrieve(reader, "hinge inference", day=DAY, query=query)
    assert expected
    writer = LifeStore(Vault(reader.vault.root), WordEmbedder())
    original = getattr(reader, boundary)

    def replacing(*args: object, **kwargs: object) -> list:
        hits = original(*args, **kwargs)
        writer.update(DAY)
        return hits

    with monkeypatch.context() as patch:
        patch.setattr(reader, boundary, replacing)
        assert retrieve(reader, "hinge inference", day=DAY, query=query) == expected
    assert reader.rows([hit["unit_id"] for hit in expected]) == {}
    assert [hit["id"] for hit in retrieve(reader, "hinge inference", day=DAY, query=query)] == [
        hit["id"] for hit in expected
    ]


@pytest.mark.parametrize("model, dim", [("test/words-v2", 64), ("test/words-v2", 32), ("test/words-v1", 32)])
def test_a_running_store_rejects_an_external_embedding_model_change(
    vector_store: LifeStore, model: str, dim: int
) -> None:
    reader = vector_store
    reader.ensure([DAY])
    assert reader.nearest("citation panel", day=DAY, k=3)
    writer = LifeStore(Vault(reader.vault.root), WordEmbedder(model, dim=dim))
    writer.ensure([DAY])
    expected = writer.nearest("citation panel", day=DAY, k=3)
    assert expected
    for _ in range(2):
        with pytest.raises(EmbeddingModelChanged, match="Restart the API"):
            reader.nearest("citation panel", day=DAY, k=3)
    with pytest.raises(EmbeddingModelChanged):
        reader.update(DAY)
    assert writer.nearest("citation panel", day=DAY, k=3) == expected


def test_a_model_change_during_rescoring_finishes_the_existing_snapshot(
    vector_store: LifeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = vector_store
    reader.ensure([DAY])
    before = reader.nearest("citation panel", day=DAY, k=3)
    writer = LifeStore(Vault(reader.vault.root), WordEmbedder("test/words-v2", dim=32))
    rescore = vectors._rescore

    def change_model(db: sqlite3.Connection, query: bytes, candidates: list[int], k: int) -> list:
        writer.ensure([DAY])
        return rescore(db, query, candidates, k)

    monkeypatch.setattr(vectors, "_rescore", change_model)
    assert reader.nearest("citation panel", day=DAY, k=3) == before
    with pytest.raises(EmbeddingModelChanged):
        reader.nearest("citation panel", day=DAY, k=3)


def test_an_old_writer_cannot_publish_after_a_model_switch_during_embedding(
    vector_store: LifeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = vector_store
    reader.ensure([DAY])
    reader.vault.append_events(
        DAY, [dict(reader.vault.events(DAY)[0], id="ev_model_switch", label="Nebula receptor", text="Nebula receptor")]
    )
    writer = LifeStore(Vault(reader.vault.root), WordEmbedder("test/words-v2", dim=32))
    embed = reader.embedder.passages

    def switch_after_embedding(texts: list[str]) -> list[bytes]:
        result = embed(texts)
        writer.ensure([DAY])
        return result

    monkeypatch.setattr(reader.embedder, "passages", switch_after_embedding)
    with pytest.raises(EmbeddingModelChanged):
        reader.update(DAY)
    assert writer.nearest("Nebula receptor", day=DAY, k=1)
    with writer.connect() as db:
        assert {row[0] for row in db.execute("SELECT length(embedding) FROM vectors")} == {32}


@pytest.mark.parametrize("suffix", ["", "?stream=1"])
def test_model_mismatch_is_an_explicit_unavailable_response(vault: Vault, auth: dict[str, str], suffix: str) -> None:
    app = create_app(Settings(vault_root=vault.root, _env_file=None), embedder=WordEmbedder())
    with TestClient(app) as client:
        app.state.store.ensure([DAY])
        writer = LifeStore(Vault(vault.root), WordEmbedder("test/words-v2", dim=32))
        writer.ensure([DAY])
        response = client.post("/ask" + suffix, json={"date": DAY, "question": "citation panel"}, headers=auth)
        assert response.status_code == 503
        assert "database embedding model changed" in response.json()["detail"]


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


def test_note_changed_during_a_read_snapshot_is_not_returned(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LifeStore(vault)
    store.notes_root.mkdir()
    path = store.notes_root / "Recipe.md"
    path.write_text("Cobalt recipe.\n", encoding="utf-8")
    store.ensure()
    keyword = store.note_keyword

    def edit_after_keyword(*args: object, **kwargs: object) -> list[int]:
        ids = keyword(*args, **kwargs)
        assert ids
        path.write_text("A navy recipe with different ingredients.\n", encoding="utf-8")
        return ids

    with monkeypatch.context() as patch:
        patch.setattr(store, "note_keyword", edit_after_keyword)
        assert retrieve(store, "Cobalt") == []
    assert retrieve(store, "navy recipe")[0]["type"] == "note.text"

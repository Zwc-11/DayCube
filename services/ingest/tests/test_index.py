import sqlite3
from contextlib import closing

import pytest
from conftest import DAY
from ingest.ask.retrieve import reciprocal_rank_fusion, retrieve
from ingest.index.build import UNIT_COLUMNS, collect_units
from ingest.store import LifeStore


def stored_units(store: LifeStore) -> list[dict]:
    with closing(sqlite3.connect(store.path)) as db:
        db.row_factory = sqlite3.Row
        return [{c: row[c] for c in UNIT_COLUMNS} for row in db.execute("SELECT * FROM units ORDER BY id")]


def test_index_is_repeatably_rebuildable_on_windows(store: LifeStore) -> None:
    first = store.update(DAY)
    assert first == store.update(DAY)
    assert first["units"] == 28
    retrieve(store, "demo", day=DAY)
    assert store.update(DAY) == first


def test_rrf_does_not_count_duplicate_results_twice() -> None:
    ranking = reciprocal_rank_fusion([["a", "b", "a"], ["b", "c"]])
    assert ranking[0][0] == "b"
    assert dict(ranking)["a"] == 1 / 61


def test_rrf_ties_follow_the_first_ranking() -> None:
    assert [item for item, _ in reciprocal_rank_fusion([[3, 1], [2, 4]])] == [3, 2, 1, 4]


def test_stored_units_equal_collected_evidence_and_fts_is_consistent(store: LifeStore) -> None:
    store.update(DAY)
    assert stored_units(store) == collect_units(store.vault, DAY)
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("INSERT INTO units_fts(units_fts, rank) VALUES ('integrity-check', 1)")
    hits = retrieve(store, "demo", day=DAY)
    assert hits and {hit["id"] for hit in hits} <= {unit["evidence_id"] for unit in collect_units(store.vault, DAY)}
    assert all(hit["matched"] == ["keyword"] for hit in hits)


def test_failed_rebuild_keeps_previous_searchable_rows(store: LifeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    import ingest.store.db as store_db

    store.update(DAY)
    before = stored_units(store)
    expected_hits = retrieve(store, "demo", day=DAY)
    unit = collect_units(store.vault, DAY)[0]
    monkeypatch.setattr(store_db, "collect_units", lambda *_: [unit, unit])
    with pytest.raises(sqlite3.IntegrityError):
        store.update(DAY)
    assert stored_units(store) == before
    assert retrieve(store, "demo", day=DAY) == expected_hits

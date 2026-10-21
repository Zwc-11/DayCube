import sqlite3
from contextlib import closing

import pytest
from ingest.ask.retrieve import reciprocal_rank_fusion, retrieve
from ingest.index.build import build_index, collect_units
from ingest.vault import Vault


def test_index_is_repeatably_rebuildable_on_windows(vault: Vault) -> None:
    first = build_index(vault, "2026-10-20")
    assert first == build_index(vault, "2026-10-20")
    assert first["units"] == 28
    retrieve(vault, "2026-10-20", "demo")
    assert build_index(vault, "2026-10-20") == first


def test_rrf_does_not_count_duplicate_results_twice() -> None:
    ranking = reciprocal_rank_fusion([["a", "b", "a"], ["b", "c"]])
    assert ranking[0][0] == "b"
    assert dict(ranking)["a"] == 1 / 61


def test_batched_index_preserves_evidence_rows_and_fts(vault: Vault) -> None:
    day = "2026-10-20"
    expected = collect_units(vault, day)
    build_index(vault, day)
    with closing(sqlite3.connect(vault.day_path(day) / "index.sqlite")) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM units ORDER BY id")]
        assert rows == [dict(unit, id=index) for index, unit in enumerate(expected, start=1)]
        db.execute("INSERT INTO units_fts(units_fts,rank) VALUES ('integrity-check',1)")
    hits = retrieve(vault, day, "demo")
    assert hits
    assert {hit["id"] for hit in hits} <= {unit["evidence_id"] for unit in expected}


def test_failed_rebuild_keeps_previous_searchable_index(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    import ingest.index.build as index_build

    day = "2026-10-20"
    build_index(vault, day)
    path = vault.day_path(day)
    previous = (path / "index.sqlite").read_bytes()
    stamp = (path / "index.stamp.json").read_bytes()
    expected_hits = retrieve(vault, day, "demo")
    unit = collect_units(vault, day)[0]
    monkeypatch.setattr(index_build, "collect_units", lambda *_: [unit, unit])
    with pytest.raises(sqlite3.IntegrityError):
        build_index(vault, day)
    assert (path / "index.sqlite").read_bytes() == previous
    assert (path / "index.stamp.json").read_bytes() == stamp
    assert not list(path.glob(".index-*"))
    assert retrieve(vault, day, "demo") == expected_hits

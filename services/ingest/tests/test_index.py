from ingest.ask.retrieve import reciprocal_rank_fusion, retrieve
from ingest.index.build import build_index


def test_index_is_repeatably_rebuildable_on_windows(vault):
    first = build_index(vault, "2026-10-20")
    assert first == build_index(vault, "2026-10-20")
    assert first["units"] == 28
    retrieve(vault, "2026-10-20", "demo")
    assert build_index(vault, "2026-10-20") == first


def test_rrf_does_not_count_duplicate_results_twice():
    ranking = reciprocal_rank_fusion([["a", "b", "a"], ["b", "c"]])
    assert ranking[0][0] == "b"
    assert dict(ranking)["a"] == 1 / 61

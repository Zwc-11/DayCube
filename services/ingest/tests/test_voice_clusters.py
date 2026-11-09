"""Cached row winners preserve the original exact average-linkage and cannot-link decisions."""

import numpy as np
import pytest
from ingest import voices


def reference(prints: np.ndarray, recordings: list[str], same: float) -> list[int]:
    n = len(prints)
    if not n:
        return []
    sim = (prints @ prints.T).astype(np.float64)
    _, where = np.unique(np.array(recordings), return_inverse=True)
    clash = where[:, None] == where[None, :]
    size = np.ones(n)
    member = np.arange(n)
    sim[clash] = -np.inf
    while True:
        a, b = np.unravel_index(int(np.argmax(sim)), sim.shape)
        if sim[a, b] <= same:
            break
        row = (size[a] * sim[a] + size[b] * sim[b]) / (size[a] + size[b])
        clash[a] |= clash[b]
        clash[:, a] = clash[a]
        row[clash[a]] = -np.inf
        sim[a], sim[:, a] = row, row
        size[a] += size[b]
        sim[b], sim[:, b] = -np.inf, -np.inf
        member[member == b] = a
    order = {group: k for k, group in enumerate(sorted(set(member.tolist())))}
    return [order[m] for m in member.tolist()]


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("indexed", [False, True])
def test_cached_and_scanned_merges_keep_exact_labels(
    dtype: type, indexed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voices, "ROW_MAX_FROM", 0 if indexed else 1000000)
    rng = np.random.default_rng(71)
    for n in (0, 1, 2, 7, 25, 60):
        for _ in range(15):
            prints = rng.normal(size=(n, 12)).astype(dtype)
            if n:
                prints /= np.linalg.norm(prints, axis=1, keepdims=True)
            clips = [str(x) for x in rng.integers(0, max(1, n // 3), size=n)]
            threshold = float(rng.choice([0.1, 0.5, 0.95]))
            assert voices.cluster(prints, clips, same=threshold) == reference(prints, clips, threshold)


def test_equal_scores_keep_row_major_ties_and_same_recording_constraints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voices, "ROW_MAX_FROM", 0)
    for n in (2, 3, 16, 31):
        prints = np.ones((n, 8), np.float32)
        prints /= np.linalg.norm(prints, axis=1, keepdims=True)
        for clips in ([str(i) for i in range(n)], [str(i // 2) for i in range(n)], ["one"] * n):
            result = voices.cluster(prints, clips)
            assert result == reference(prints, clips, 0.5)
            for label in set(result):
                members = [clips[i] for i, group in enumerate(result) if group == label]
                assert len(members) == len(set(members))

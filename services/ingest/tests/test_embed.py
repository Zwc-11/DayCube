"""The meaning-search embedder (ingest/index/embed.py) with a stand-in for fastembed: texts are
embedded shortest first in small batches and come back in their own order, and the model is let
go after a while unused (never during a run) and loaded again by the next search."""

from __future__ import annotations

import sys
import time
import types
from collections.abc import Iterator

import numpy as np
import pytest
from ingest.index import embed


class FakeModel:
    """Records the batches it is given; a text's vector is its length."""

    loaded = 0

    def __init__(self, model_name: str, **options: object) -> None:
        FakeModel.loaded += 1
        self.batches: list[list[int]] = []

    def embed(self, texts: list[str], batch_size: int = 256) -> Iterator[np.ndarray]:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            self.batches.append([len(t) for t in batch])
            yield from (np.full(4, len(t) / 1000) for t in batch)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> type[FakeModel]:
    FakeModel.loaded = 0
    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=FakeModel))
    return FakeModel


def test_passages_are_embedded_shortest_first_and_returned_in_their_order(fake: type[FakeModel]) -> None:
    texts = ["x" * n for n in (90, 5, 300, 40, 5, 120) * 7]
    embedder = embed.FastEmbedder()
    vectors = embedder.passages(texts)
    assert vectors == [embed.FastEmbedder._quantize(np.full(4, len(t) / 1000)) for t in texts]
    batches = embedder._model.batches
    lengths = [n for batch in batches for n in batch]
    assert lengths == sorted(lengths) and max(map(len, batches)) <= embed.BATCH


def test_the_model_is_kept_while_in_use_let_go_when_unused_and_loaded_again(
    fake: type[FakeModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = embed.FastEmbedder(idle_s=0.05)
    loaded_during_run = []

    def slow(self: FakeModel, texts: list[str], batch_size: int = 256) -> Iterator[np.ndarray]:
        for _ in texts:
            time.sleep(0.03)
            loaded_during_run.append(embedder._model is not None)
            yield np.full(4, 0.1)

    monkeypatch.setattr(FakeModel, "embed", slow)
    embedder.passages(["x"] * 8)  # longer than idle_s
    embedder.query("second")
    assert all(loaded_during_run) and fake.loaded == 1
    deadline = time.monotonic() + 5
    while embedder._model is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert embedder._model is None
    embedder.query("after a while")
    assert fake.loaded == 2

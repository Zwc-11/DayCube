"""Finding items by words: "a wooden bench", "where are my keys".

A query is compared two ways and the two are added: its text embedding against each item's image
embedding (the embedder puts pictures and the words for them near each other), and its words
against the names the detector gave the item. The embedding finds things by what they look like
even when the detector called them something else; the names keep an exact word on top.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import store

NAME_BONUS = 0.08  # added per query word found in the item's names, at most twice


def score(query: str, record: dict, vectors: np.ndarray, embed: Callable[[list[str]], np.ndarray]) -> np.ndarray:
    if not len(vectors):
        return np.zeros(0)
    text = embed([f"a photo of {query}"])[0]
    sims = vectors @ text
    words = {w for w in query.lower().split() if len(w) > 2}
    for k, item in enumerate(record["items"]):
        names = " ".join(item["labels"])
        sims[k] += NAME_BONUS * min(2, sum(w in names for w in words))
    return sims


def search(query: str, folders: list[Path], embed: Callable[[list[str]], np.ndarray], top: int = 5) -> list[dict]:
    """The best ``top`` items over every items folder given: each with its score, its set and where
    and when it was seen."""
    hits = []
    for folder in folders:
        record, vectors = store.read(folder)
        sims = score(query, record, vectors, embed)
        for k in np.argsort(-sims)[:top]:
            item = record["items"][int(k)]
            hits.append({"score": round(float(sims[k]), 4), "set": record["id"], "folder": str(folder), **item})
    return sorted(hits, key=lambda h: -h["score"])[:top]

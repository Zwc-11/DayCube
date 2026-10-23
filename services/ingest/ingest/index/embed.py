"""Text embeddings for meaning search, stored as int8.

bge-small-en-v1.5 gives unit-length 384-d float vectors. Stored as float32 that is 1.5 KB a unit,
about 2.9 GB for a year at the spec's capture rate; int8 is a quarter of that. The components
are small (median magnitude 0.027, 99.9th percentile 0.37), so they are scaled by ``SCALE``
before rounding: on 1,218 sentences from the repository's documents, the int8 top-10 neighbours
agree with the float ones 97.6% of the time at 254, against 96.6% at 127 and 94% at 381, where
clipping starts to bite. Cosine distance ignores the scale, so it never has to be undone.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from typing import Protocol

SCALE = 254.0
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
# bge v1.5 is trained with this instruction on short queries; passages are embedded bare.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    name: str

    @property
    def dim(self) -> int: ...

    def passages(self, texts: Sequence[str]) -> list[bytes]:
        """One int8 vector per text, as raw bytes (``dim`` signed bytes)."""
        ...

    def query(self, text: str) -> bytes: ...


class FastEmbedder:
    """bge-small on CPU through fastembed (ONNX Runtime). The model is loaded, and the first time
    downloaded (about 130 MB), on first use rather than at construction, so importing the API or
    running a command that never searches stays instant."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.name = model
        self._model = None
        self._dim: int | None = None

    def _load(self) -> object:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.name)
        return self._model

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.query("dimension probe"))
        return self._dim

    @staticmethod
    def _quantize(vector: object) -> bytes:
        import numpy as np

        return np.clip(np.rint(np.asarray(vector) * SCALE), -127, 127).astype(np.int8).tobytes()

    def passages(self, texts: Sequence[str]) -> list[bytes]:
        return [self._quantize(vector) for vector in self._load().embed(list(texts), batch_size=64)]

    def query(self, text: str) -> bytes:
        return self._quantize(next(iter(self._load().embed([QUERY_PREFIX + text]))))


def default_embedder(enabled: bool, model: str = DEFAULT_MODEL) -> Embedder | None:
    """The local model when meaning search is on and its libraries are installed, else None:
    search then runs on keywords alone. Install with ``uv sync --extra vector``."""
    if not enabled or importlib.util.find_spec("fastembed") is None:
        return None
    return FastEmbedder(model)

"""Text embeddings for meaning search, stored as int8.

bge-small-en-v1.5 gives unit-length 384-d float vectors. Stored as float32 that is 1.5 KB a unit,
about 2.9 GB for a year at the spec's capture rate; int8 is a quarter of that. The components
are small (median magnitude 0.027, 99.9th percentile 0.37), so they are scaled by ``SCALE``
before rounding: on 1,218 sentences from the repository's documents, the int8 top-10 neighbours
agree with the float ones 97.6% of the time at 254, against 96.6% at 127 and 94% at 381, where
clipping starts to bite. Cosine distance ignores the scale, so it never has to be undone.
"""

from __future__ import annotations

import contextlib
import ctypes
import importlib.util
import os
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from typing import Protocol

SCALE = 254.0
# ONNX Runtime pads a batch to its longest text and attention grows with the square of that
# length, so texts go shortest first, a few at a time: on 2,000 texts of real lengths 6.3x faster
# than unsorted batches of 64, at under a third of the peak memory (eval/benchmark_embed.py).
BATCH = 16
# A quarter of the logical processors (at least 2): embedding a day runs beside the eye tracker
# and capture, which keep the rest; a question's one short text takes milliseconds either way.
THREADS = max(2, (os.cpu_count() or 2) // 4)
IDLE_S = 600.0  # unused this long, the model is let go: about 190 MB of the API's memory
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
    running a command that never searches stays instant; ``idle_s`` after its last use it is let
    go, and the next search loads it again (about a quarter of a second)."""

    def __init__(self, model: str = DEFAULT_MODEL, *, idle_s: float = IDLE_S) -> None:
        self.name, self.idle_s = model, idle_s
        self._model = None
        self._dim: int | None = None
        self._lock = threading.Lock()
        self._runs, self._used = 0, 0.0
        self._timer: threading.Timer | None = None

    @contextlib.contextmanager
    def _in_use(self) -> Iterator[object]:
        """The model, loaded if need be, and kept while a run uses it."""
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(model_name=self.name, threads=THREADS)
            if self._timer is None:
                self._release_in(self.idle_s)
            self._runs += 1
            model = self._model
        try:
            yield model
        finally:
            with self._lock:
                self._runs, self._used = self._runs - 1, time.monotonic()

    def _release_in(self, seconds: float) -> None:
        self._timer = threading.Timer(seconds, self._release)
        self._timer.daemon = True
        self._timer.start()

    def _release(self) -> None:
        with self._lock:
            unused = time.monotonic() - self._used
            if self._runs or unused < self.idle_s:
                self._release_in(self.idle_s if self._runs else self.idle_s - unused)
                return
            self._model = self._timer = None
        _give_back()

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
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out = [b""] * len(texts)
        with self._in_use() as model:
            for i, vector in zip(order, model.embed([texts[i] for i in order], batch_size=BATCH), strict=True):
                out[i] = self._quantize(vector)
        return out

    def query(self, text: str) -> bytes:
        with self._in_use() as model:
            return self._quantize(next(iter(model.embed([QUERY_PREFIX + text]))))


def _give_back() -> None:
    """Return the freed model's memory to the system: glibc keeps freed pages in the process
    (about 190 MB of bge-small's); Windows' heap gives large blocks back by itself."""
    if sys.platform.startswith("linux"):
        with contextlib.suppress(OSError):
            ctypes.CDLL("libc.so.6").malloc_trim(0)


def default_embedder(enabled: bool, model: str = DEFAULT_MODEL) -> Embedder | None:
    """The local model when meaning search is on and its libraries are installed, else None:
    search then runs on keywords alone. Install with ``uv sync --extra vector``."""
    if not enabled or importlib.util.find_spec("fastembed") is None:
        return None
    return FastEmbedder(model)

"""Nearest-neighbour search over the vault's int8 vectors, in two passes.

The vectors live in the vault database as plain blobs (``vectors``: the int8 embedding and its
sign bits). Searching them inside SQLite does not scale: sqlite-vec's brute-force KNN over a
year of units (1.93M) took 1.5 s on two CPU cores, and its bit vectors were no faster, because
its per-row overhead, not the arithmetic, dominates.

So the sign bits are held in memory, column-major (one contiguous uint64 array per 64 bits of
the vector) and sorted by time within one block per day:

1. **Coarse pass:** Hamming distance between the question's sign bits and every unit in scope,
   by XOR and popcount over whole arrays: 32 ms for a year on two cores, under 1 ms for a week,
   which is a contiguous slice of each day's block.
2. **Exact pass:** the best ``oversample * k`` candidates are re-scored by int8 cosine, fetched
   by primary key.

On 6,000 real sentences, re-scoring 16x the wanted count kept 96-97% of the exact float top-k
(recall@10 and @50); this module re-scores 40x, since a year holds 300 times more units. Memory
is 64 bytes a unit (bits, id, time): about 125 MB for a year.

Blocks are reloaded from the database when a day's ``generation`` changes, so a write by another
process (``daycube index``) is picked up by the next search.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from ..cache import SegmentedLRU

OVERSAMPLE = 40
CHUNK = 500
CACHE_BYTES = 64 << 20
ENTRY_BYTES = 512  # allowance for the key, tuple, array headers and cache bookkeeping


def sign_bits(vector: bytes) -> bytes:
    """The vector's sign pattern, packed: 48 bytes for 384 dimensions."""
    return np.packbits(np.frombuffer(vector, np.int8) > 0).tobytes()


def _words(bits: bytes) -> np.ndarray:
    """Packed bits as uint64 words, padded to a whole word."""
    padded = bits + b"\0" * (-len(bits) % 8)
    return np.frombuffer(padded, np.uint64)


@dataclass(frozen=True, slots=True)
class Block:
    ids: np.ndarray  # int64, sorted by ts
    ts: np.ndarray  # float64 seconds since the epoch
    words: np.ndarray  # uint64, (words per vector, units): one contiguous row per word

    @classmethod
    def load(cls, db: sqlite3.Connection, day: str) -> Block:
        rows = db.execute(
            "SELECT u.id, u.ts, v.bits FROM units u JOIN vectors v ON v.id = u.id WHERE u.day = ? ORDER BY u.ts, u.id",
            (day,),
        ).fetchall()
        if not rows:
            return cls(np.empty(0, np.int64), np.empty(0), np.empty((0, 0), np.uint64))
        padding = b"\0" * (-len(rows[0][2]) % 8)
        packed = np.frombuffer(b"".join(row[2] + padding for row in rows), np.uint64)
        return cls(
            np.fromiter((row[0] for row in rows), np.int64, len(rows)),
            np.fromiter((row[1] for row in rows), np.float64, len(rows)),
            np.ascontiguousarray(packed.reshape(len(rows), -1).T),
        )


class VectorIndex:
    """Scoped sign-bit blocks, keyed by the database's non-reused day generation.

    Only requested days load. Retained arrays are size-bounded; an oversized day's
    working block can exceed the budget during that search but is never cached.
    Old generations expire through eviction and are never selected for a new search.
    """

    def __init__(self, cache_bytes: int = CACHE_BYTES) -> None:
        self._cache: SegmentedLRU[tuple[str, int], Block] = SegmentedLRU(cache_bytes)
        self._lock = threading.Lock()

    @property
    def cache_size_bytes(self) -> int:
        """Retained array bytes plus a fixed allowance per cache entry, not process RSS."""
        return self._cache.size

    def clear(self) -> None:
        with self._lock:
            self._cache = SegmentedLRU(self._cache.capacity)

    def search(
        self,
        db: sqlite3.Connection,
        query: bytes,
        k: int,
        *,
        days: Iterable[str] | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[tuple[int, float]]:
        """(unit id, cosine similarity) of the ``k`` nearest units in scope, nearest first."""
        if k < 0:
            raise ValueError("k must be nonnegative")
        if k == 0:
            return []
        with self._lock:
            current = dict(db.execute("SELECT day, generation FROM days").fetchall())
            scope = current if days is None else days
            q = _words(sign_bits(query))
            want = OVERSAMPLE * k
            ids, distances = [], []
            for day in scope:
                if day not in current:
                    continue
                key = day, current[day]
                block = self._cache.get(key)
                if block is None:
                    block = Block.load(db, day)
                    size = block.ids.nbytes + block.ts.nbytes + block.words.nbytes + ENTRY_BYTES
                    self._cache.put(key, block, size)
                lo = 0 if since is None else int(np.searchsorted(block.ts, since, "left"))
                hi = len(block.ts) if until is None else int(np.searchsorted(block.ts, until, "left"))
                if hi <= lo:
                    continue
                d = np.bitwise_count(block.words[0, lo:hi] ^ q[0]).astype(np.uint16)
                for word in range(1, len(q)):
                    d += np.bitwise_count(block.words[word, lo:hi] ^ q[word])
                if hi - lo > want:
                    keep = np.argpartition(d, want)[:want]
                    d, chosen = d[keep], block.ids[lo:hi][keep]
                else:
                    # Candidates must not retain a view into an evicted day's arrays.
                    chosen = block.ids[lo:hi].copy()
                ids.append(chosen)
                distances.append(d)
        if not ids:
            return []
        candidates, d = np.concatenate(ids), np.concatenate(distances)
        if len(candidates) > want:
            candidates = candidates[np.argpartition(d, want)[:want]]
        return _rescore(db, query, candidates.tolist(), k)


def _rescore(db: sqlite3.Connection, query: bytes, candidates: list[int], k: int) -> list[tuple[int, float]]:
    rows = []
    for start in range(0, len(candidates), CHUNK):
        chunk = candidates[start : start + CHUNK]
        marks = ",".join("?" for _ in chunk)
        rows += db.execute(f"SELECT id, embedding FROM vectors WHERE id IN ({marks})", chunk).fetchall()
    matrix = np.frombuffer(b"".join(row[1] for row in rows), np.int8).reshape(len(rows), -1).astype(np.float32)
    q = np.frombuffer(query, np.int8).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1) * max(float(np.linalg.norm(q)), 1e-9)
    similarity = (matrix @ q) / np.maximum(norms, 1e-9)
    order = np.argsort(-similarity, kind="stable")[:k]
    return [(rows[i][0], float(similarity[i])) for i in order]

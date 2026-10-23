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

OVERSAMPLE = 40
CHUNK = 500


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
        packed = np.frombuffer(b"".join(_words(row[2]).tobytes() for row in rows), np.uint64)
        return cls(
            np.fromiter((row[0] for row in rows), np.int64, len(rows)),
            np.fromiter((row[1] for row in rows), np.float64, len(rows)),
            np.ascontiguousarray(packed.reshape(len(rows), -1).T),
        )


class VectorIndex:
    """Sign-bit blocks per day, kept in step with the database's ``days.generation``."""

    def __init__(self) -> None:
        self._blocks: dict[str, Block] = {}
        self._generations: dict[str, int] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._blocks.clear()
            self._generations.clear()

    def _sync(self, db: sqlite3.Connection) -> None:
        current = dict(db.execute("SELECT day, generation FROM days").fetchall())
        for day in set(self._blocks) - set(current):
            del self._blocks[day], self._generations[day]
        for day, generation in current.items():
            if self._generations.get(day) != generation:
                self._blocks[day] = Block.load(db, day)
                self._generations[day] = generation

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
        with self._lock:
            self._sync(db)
            blocks = [self._blocks[d] for d in days if d in self._blocks] if days is not None else self._blocks.values()
            q = _words(sign_bits(query))
            want = OVERSAMPLE * k
            ids, distances = [], []
            for block in blocks:
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
                    chosen = block.ids[lo:hi]
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

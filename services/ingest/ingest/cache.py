"""A size-bounded, scan-resistant cache for parsed days and serialized payloads."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import PurePath
from sys import getsizeof
from threading import Lock
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class SegmentedLRU(Generic[K, V]):
    """Segmented LRU (Karedla, Love and Wherry, 1994) bounded by the total size of its entries.

    A new entry enters *probation*. A second hit promotes it to *protected*, which holds at most
    ``protected_share`` of the capacity; overflow there demotes the least recent protected entry
    back to probation, and eviction takes from probation first. A single pass over many keys,
    such as reindexing or exporting every day, only cycles probation, so the days a person keeps
    returning to stay resident. Lookups and recency updates are O(1); insertion may evict several
    entries. Metadata admission also walks the supplied value to account for its retained size.

    The access pattern here is a handful of hot days, strongly recency-biased, with occasional
    scans. That rules out plain LRU (scans flush it) and gives frequency sketches such as
    TinyLFU too few accesses per key to learn from.
    """

    def __init__(self, capacity: int, protected_share: float = 0.8) -> None:
        if capacity <= 0 or not 0 < protected_share < 1:
            raise ValueError("capacity must be positive and protected_share in (0, 1)")
        self.capacity = capacity
        self.protected_capacity = int(capacity * protected_share)
        self._probation: OrderedDict[K, tuple[V, int]] = OrderedDict()
        self._protected: OrderedDict[K, tuple[V, int]] = OrderedDict()
        self._sizes = [0, 0]  # probation, protected
        self._lock = Lock()

    @property
    def size(self) -> int:
        return self._sizes[0] + self._sizes[1]

    def __len__(self) -> int:
        return len(self._probation) + len(self._protected)

    def __contains__(self, key: object) -> bool:
        return key in self._probation or key in self._protected

    def get(self, key: K) -> V | None:
        with self._lock:
            entry = self._protected.get(key)
            if entry is not None:
                self._protected.move_to_end(key)
                return entry[0]
            entry = self._probation.pop(key, None)
            if entry is None:
                return None
            self._sizes[0] -= entry[1]
            self._protected[key] = entry
            self._sizes[1] += entry[1]
            while self._sizes[1] > self.protected_capacity and len(self._protected) > 1:
                demoted, old = self._protected.popitem(last=False)
                self._sizes[1] -= old[1]
                self._probation[demoted] = old
                self._sizes[0] += old[1]
            self._evict()
            return entry[0]

    def put(self, key: K, value: V, size: int) -> None:
        """Insert or replace. An entry larger than the whole capacity is not kept."""
        if size < 0:
            raise ValueError("entry size must be nonnegative")
        with self._lock:
            self._discard(key)
            if size > self.capacity:
                return
            self._probation[key] = (value, size)
            self._sizes[0] += size
            self._evict()

    def put_metadata(self, key: K, value: V) -> None:
        """Charge Python metadata recursively, plus 512 bytes for cache bookkeeping.

        Used for stamps and small records, never native arrays. This bounds accounted retained
        objects, not RSS: the allocator, shared objects and caller-owned working sets differ.
        """
        stack: list[object] = [key, value]
        seen: set[int] = set()
        size = 512
        while stack:
            item = stack.pop()
            if id(item) in seen:
                continue
            seen.add(id(item))
            size += getsizeof(item)
            if isinstance(item, dict):
                stack.extend(item.keys())
                stack.extend(item.values())
            elif isinstance(item, (tuple, list, set, frozenset)):
                stack.extend(item)
            elif isinstance(item, PurePath):
                # Include the path's strings, which are stored behind slots rather than in a dict.
                stack.extend((str(item), item.parts))
        self.put(key, value, size)

    def clear(self) -> None:
        with self._lock:
            self._probation.clear()
            self._protected.clear()
            self._sizes[:] = [0, 0]

    def pop(self, key: K) -> None:
        with self._lock:
            self._discard(key)

    def _discard(self, key: K) -> None:
        for segment, entries in enumerate((self._probation, self._protected)):
            entry = entries.pop(key, None)
            if entry is not None:
                self._sizes[segment] -= entry[1]

    def _evict(self) -> None:
        while self.size > self.capacity:
            segment = 0 if self._probation else 1
            _, old = (self._probation, self._protected)[segment].popitem(last=False)
            self._sizes[segment] -= old[1]

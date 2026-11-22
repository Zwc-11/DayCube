"""Observations joined into items: the same thing seen from several frames is one entry.

Two observations are the same thing when they are near each other in space and look alike. Near
is judged against their own size (a tree's observations may lie a metre apart, a cup's cannot);
alike is the cosine of their image embeddings. Each test alone is not enough: a row of identical
chairs look alike, and a cup on a table sits inside the table's box. Two boxes in the same frame
are never the same thing, however alike and near: that is what keeps a row of bottles apart.

Observations are taken in time order and joined to the best item within reach, then items are
joined to each other once more (a thing seen at the start and at the end of a walk). An item's
place is the median of its observations' places; ``spread_m`` is the median distance of those from
it, the measure of how well the frames agree.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

ALIKE = 0.72  # cosine of two crops' embeddings above which they may be the same thing
REACH = 0.75  # of the larger of the two things' sizes ...
REACH_MIN_M, REACH_MAX_M = 0.35, 2.5  # ... but at least and at most this far apart


@dataclass
class Observation:
    frame: int
    t: float
    box: tuple[float, float, float, float]
    label: str
    score: float
    position: np.ndarray  # (3,)
    size_m: tuple[float, float]
    distance_m: float
    depth: str
    vector: np.ndarray  # unit image embedding


@dataclass
class Item:
    observations: list[Observation] = field(default_factory=list)

    @property
    def position(self) -> np.ndarray:
        return np.median([o.position for o in self.observations], 0)

    @property
    def size(self) -> float:
        return float(np.median([max(o.size_m) for o in self.observations]))

    @property
    def vector(self) -> np.ndarray:
        v = np.mean([o.vector for o in self.observations], 0)
        return v / (np.linalg.norm(v) + 1e-12)

    @property
    def label(self) -> str:
        return Counter(o.label for o in self.observations).most_common(1)[0][0]

    @property
    def spread_m(self) -> float:
        if len(self.observations) < 2:
            return 0.0
        p = self.position
        return float(np.median([np.linalg.norm(o.position - p) for o in self.observations]))

    def best(self) -> Observation:
        """The observation to show: the closest look that is not cut by the frame's edge is not
        known here, so the nearest with the highest score."""
        return max(self.observations, key=lambda o: (o.score, -o.distance_m))


def _reach(a: float, b: float) -> float:
    return float(np.clip(REACH * max(a, b), REACH_MIN_M, REACH_MAX_M))


def merge(observations: list[Observation], alike: float = ALIKE) -> list[Item]:
    items: list[Item] = []
    seen: list[set[int]] = []  # per item, the frames it was seen in
    positions = np.zeros((0, 3))
    vectors = np.zeros((0, len(observations[0].vector))) if observations else np.zeros((0, 1))
    sizes: list[float] = []

    def refresh(k: int) -> None:
        positions[k], vectors[k], sizes[k] = items[k].position, items[k].vector, items[k].size

    for o in sorted(observations, key=lambda o: o.t):
        best, best_score = -1, -1.0
        if items:
            d = np.linalg.norm(positions - o.position, axis=1)
            cos = vectors @ o.vector
            for k in np.flatnonzero(cos >= alike):
                if o.frame in seen[k]:
                    continue
                if d[k] <= _reach(sizes[k], max(o.size_m)) and cos[k] - 0.1 * d[k] > best_score:
                    best, best_score = int(k), float(cos[k] - 0.1 * d[k])
        if best < 0:
            items.append(Item([o]))
            positions = np.vstack([positions, o.position])
            vectors = np.vstack([vectors, o.vector])
            sizes.append(max(o.size_m))
            seen.append({o.frame})
        else:
            items[best].observations.append(o)
            seen[best].add(o.frame)
            refresh(best)
    # Items joined to items: the same thing reached again later, by then an item of its own.
    joined = True
    while joined:
        joined = False
        for a in range(len(items)):
            if not items[a].observations:
                continue
            for b in range(a + 1, len(items)):
                if not items[b].observations or seen[a] & seen[b]:
                    continue
                if vectors[a] @ vectors[b] >= alike and np.linalg.norm(positions[a] - positions[b]) <= _reach(
                    sizes[a], sizes[b]
                ):
                    items[a].observations += items[b].observations
                    items[b].observations = []
                    seen[a] |= seen[b]
                    refresh(a)
                    joined = True
    return [item for item in items if item.observations]

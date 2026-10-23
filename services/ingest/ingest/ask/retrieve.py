"""Find the evidence for a question: exact words and close meaning, merged by rank.

Keyword search (FTS5, BM25) finds names, numbers and quoted phrases; meaning search (bge-small
vectors) finds paraphrases that share no word with the record. Their rankings are merged with
reciprocal-rank fusion, which needs no score calibration between the two.

A vector index always returns its nearest neighbours, related or not, so meaning-only hits must
clear ``MIN_SIMILARITY``. That is a coarse filter, not a "no answer" detector: on the sample
day, correct answers score 0.48-0.80 and the best match for questions the day cannot answer
scores up to 0.60, so the two ranges overlap. Deciding that nothing answers a question is the
answer checker's job.
"""

from __future__ import annotations

import re
from collections.abc import Hashable, Mapping, Sequence

from ..store import LifeStore
from ..time import tod
from ..vault import Record

# fmt: off
STOP_WORDS = frozenset((
    "a", "an", "the", "i", "me", "my", "we", "our", "you", "your", "was", "were", "is", "are", "am",
    "when", "where", "what", "who", "how", "why", "did", "do", "does", "have", "had", "with", "about",
    "at", "in", "on", "of", "to", "for", "it", "that", "this", "and", "or", "as", "be", "been", "can",
    "could", "tell", "decided",
))
# fmt: on
CANDIDATES = 50  # per method, before fusion
MIN_SIMILARITY = 0.55  # cosine, with the bge query instruction; see the module docstring


def terms(question: str) -> list[str]:
    words = re.findall(r"[^\W_]+", question.casefold(), flags=re.UNICODE)
    return list(dict.fromkeys(w for w in words if len(w) > 1 and w not in STOP_WORDS))[:16]


def retrieve(
    store: LifeStore,
    question: str,
    *,
    day: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = 10,
) -> list[Record]:
    """Evidence for ``question`` across the vault, or one ``day``, optionally between ``since``
    and ``until`` (seconds since the epoch). Each record says which method found it."""
    query_terms = terms(question)
    if not query_terms and not store.vectors:
        return []
    store.ensure(None if day is None else [day])
    scope = {"day": day, "since": since, "until": until, "k": CANDIDATES}
    # Quote every token: user punctuation can never become an FTS operator.
    keyword = store.keyword(" OR ".join(f'"{w}"' for w in query_terms), **scope) if query_terms else []
    meaning = [unit for unit, similarity in store.nearest(question, **scope) if similarity >= MIN_SIMILARITY]
    rows = store.rows(list(dict.fromkeys([*keyword, *meaning])))
    # An OR query matches a record that shares one common word ("day") with the question. Scale
    # each keyword hit by the share of the question's words it contains, so such a hit cannot tie
    # a close meaning match; a record containing every word keeps full weight.
    wanted = set(query_terms)
    coverage = {unit: len(wanted.intersection(terms(rows[unit]["text"]))) / len(wanted) for unit in keyword}
    fused = [unit for unit, _ in reciprocal_rank_fusion([keyword, meaning], weights=[coverage, None])[:limit]]
    found = {"keyword": set(keyword), "meaning": set(meaning)}
    return [_record(store, rows[unit], found) for unit in fused if unit in rows]


def _record(store: LifeStore, unit: Record, found: dict[str, set[int]]) -> Record:
    snapshot = store.vault.snapshot(unit["day"])
    place = snapshot.places.get(unit["place"])
    names = store.vault.place_names()
    return {
        "id": unit["evidence_id"],
        "unit_id": unit["id"],
        "day": unit["day"],
        "type": unit["type"],
        "t": tod(unit["t"], snapshot.day["tz"]),
        "iso_time": unit["t"],
        "place": unit["place"],
        "place_name": names.get(place["id"], place["name"]) if place else None,
        "layer_index": place["layer_index"] if place else None,
        "floor": unit["floor"],
        "x": unit["x"],
        "y": unit["y"],
        "text": unit["text"],
        "source_ref": unit["source_ref"],
        "fidelity": unit["fidelity"],
        "confidence": unit["confidence"],
        "attention": unit["attention"],
        "moment_id": unit["moment_id"],
        "offset_s": unit["offset_s"],
        "matched": [method for method, units in found.items() if unit["id"] in units],
    }


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Hashable]],
    k: int = 60,
    weights: Sequence[Mapping[Hashable, float] | None] | None = None,
) -> list[tuple[Hashable, float]]:
    """Sum of w / (k + rank) over the rankings an item appears in, with w from that ranking's
    ``weights`` (default 1); a repeat within one ranking counts once. Ties keep the order of first
    appearance, so the first ranking breaks them."""
    scores: dict[Hashable, float] = {}
    for index, ranking in enumerate(rankings):
        weight = weights[index] if weights else None
        for rank, item in enumerate(dict.fromkeys(ranking), start=1):
            scores[item] = scores.get(item, 0) + (1.0 if weight is None else weight[item]) / (k + rank)
    order = {item: index for index, item in enumerate(scores)}
    return sorted(scores.items(), key=lambda pair: (-pair[1], order[pair[0]]))

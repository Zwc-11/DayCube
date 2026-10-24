from datetime import datetime
from zoneinfo import ZoneInfo

from ..store import LifeStore
from .query import parse
from .retrieve import retrieve, terms


def ask(store: LifeStore, day: str | None, question: str, now: datetime | None = None) -> dict:
    """Answer from the best evidence in one day, or across the vault when ``day`` is None."""
    if day is not None and not store.knows(day):
        raise FileNotFoundError(f"No vault day: {day}")
    query = parse(question, now or datetime.now(ZoneInfo(store.zone)))
    evidence = retrieve(store, question, day=day, query=query, limit=20 if not terms(query.text) else 10)
    best = evidence[0] if evidence else None
    # Verbatim extraction is supported by construction. No generative claims or fake model labels.
    return {
        "mode": "extractive",
        "question": question,
        "day": best["day"] if best else day,
        "layer_index": best["layer_index"] if best else None,
        "t": best["t"] if best else None,
        "floor": best["floor"] if best else None,
        "x": best["x"] if best else None,
        "y": best["y"] if best else None,
        "moment_id": best["moment_id"] if best else None,
        "offset_s": best["offset_s"] if best else None,
        "sentences": [{"text": best["text"], "evidence": [best["id"]]}] if best else [],
        "evidence": evidence,
        # What the question's time, kind and site words were read as, and what was left to search.
        "filters": {
            "labels": [label for label in query.labels if label],
            "since": query.since,
            "until": query.until,
            "types": list(query.types) if query.types else None,
            "site": query.site,
            "search": query.text,
        },
        "message": _message(best, evidence, query, day),
    }


def _message(best: dict | None, evidence: list[dict], query: object, day: str | None) -> str | None:
    if best is None:
        return f"No matching evidence found in {'this day' if day else 'the vault'}."
    if query.text and terms(query.text) and all(e["matched"] == ["listing"] for e in evidence):
        shown = ", ".join(label for label in query.labels if label)
        return f"Nothing matched “{' '.join(terms(query.text))}”; showing {shown} instead."
    return None

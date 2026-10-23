from ..store import LifeStore
from .retrieve import retrieve


def ask(store: LifeStore, day: str | None, question: str) -> dict:
    """Answer from the best evidence in one day, or across the vault when ``day`` is None."""
    if day is not None:
        store.vault.day_path(day)
    evidence = retrieve(store, question, day=day)
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
        "message": None if best else f"No matching evidence found in {'this day' if day else 'the vault'}.",
    }

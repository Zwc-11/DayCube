from ..vault import Vault
from .retrieve import retrieve


def ask(vault: Vault, day: str, question: str) -> dict:
    vault.day_path(day)
    evidence = retrieve(vault, day, question)
    best = evidence[0] if evidence else None
    # Verbatim extraction is supported by construction. No generative claims or fake model labels.
    return {
        "mode": "extractive", "question": question,
        "layer_index": best["layer_index"] if best else None,
        "t": best["t"] if best else None, "floor": best["floor"] if best else None,
        "x": best["x"] if best else None, "y": best["y"] if best else None,
        "moment_id": best["moment_id"] if best else None,
        "offset_s": best["offset_s"] if best else None,
        "sentences": [{"text": best["text"], "evidence": [best["id"]]}] if best else [],
        "evidence": evidence,
        "message": None if best else "No matching evidence found in this day.",
    }

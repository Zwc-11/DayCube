import re
import sqlite3
from contextlib import closing

from ..index.build import ensure_index
from ..time import instant, tod
from ..vault import Vault, read_json

STOP_WORDS = set("a an the i me my we our you your was were is are am when where what who how why did do does have had with about at in on of to for it that this and or as be been can could tell decided".split())


def terms(question: str) -> list[str]:
    words = re.findall(r"[^\W_]+", question.casefold(), flags=re.UNICODE)
    return list(dict.fromkeys(w for w in words if len(w) > 1 and w not in STOP_WORDS))[:16]


def retrieve(vault: Vault, day: str, question: str, limit: int = 10) -> list[dict]:
    query_terms = terms(question)
    if not query_terms:
        return []
    with vault.lock:
        ensure_index(vault, day)
        path = vault.day_path(day)
        # Quote every token: user punctuation can never become an FTS operator.
        expression = " OR ".join(f'"{word}"' for word in query_terms)
        with closing(sqlite3.connect(path / "index.sqlite")) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("""
                SELECT u.*, bm25(units_fts) AS rank FROM units_fts
                JOIN units u ON u.id = units_fts.rowid
                WHERE units_fts MATCH ? ORDER BY rank, u.t LIMIT ?
            """, (expression, limit)).fetchall()
        data = read_json(path / "day.json")
        names = read_json(vault.root / "places.json")
        places = {p["id"]: p for p in data["places"]}
        result = []
        for row in rows:
            unit = dict(row)
            place = places.get(unit["place"])
            offset = unit["offset_s"]
            if unit["moment_id"] and offset is None:
                meta = read_json(path / "moments" / unit["moment_id"] / "meta.json")
                offset = max(0, (instant(unit["t"]) - instant(meta["t0"])).total_seconds())
            result.append({
                "id": unit["evidence_id"], "unit_id": unit["id"], "type": unit["type"],
                "t": tod(unit["t"], data["tz"]), "iso_time": unit["t"],
                "place": unit["place"], "place_name": names.get(place["id"], place["name"]) if place else None,
                "layer_index": place["layer_index"] if place else None,
                "floor": unit["floor"], "x": unit["x"], "y": unit["y"],
                "text": unit["text"], "source_ref": unit["source_ref"],
                "fidelity": unit["fidelity"], "confidence": unit["confidence"],
                "attention": unit["attention"], "moment_id": unit["moment_id"], "offset_s": offset,
            })
        return result


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, record in enumerate(dict.fromkeys(ranking), start=1):
            scores[record] = scores.get(record, 0) + 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))

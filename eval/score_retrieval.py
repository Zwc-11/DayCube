"""Retrieval on the sample day: keyword, meaning and combined search, side by side.

A sanity check on 28 units, not a benchmark: the sample day is synthetic, and the meaning-search
threshold and query instruction were chosen by looking at these same questions. The numbers that
count come from 50 questions about real days (spec 11.1).

    uv run --extra vector python eval/score_retrieval.py

Without the vector extra, only keyword search is scored.

Your own days, with questions you wrote (the gate in docs/recall.md: 70% of 30):

    uv run --extra vector python eval/score_retrieval.py --vault C:\DAYCUBE\vault --questions my.jsonl

One question a line: ``{"q": "where did I read about hash tables", "has": "open addressing"}``,
with ``"date": "2026-11-12"`` to ask about one day. ``has`` is a phrase the right record holds.
It searches as the app does (bringing the vault's database up to date first) and prints, for each
question, the rank of the first record that holds the phrase.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ingest.ask.retrieve import MIN_SIMILARITY, retrieve
from ingest.index.embed import default_embedder
from ingest.store import LifeStore
from ingest.time import instant
from ingest.vault import Vault, atomic_text

ROOT = Path(__file__).resolve().parent


def lines(name: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / name).read_text().splitlines() if line]


def located(record: dict, question: dict) -> bool:
    """The original four questions name a place and a time instead of evidence ids."""
    delta = abs((instant(record["iso_time"]) - instant(question["gt_t"])).total_seconds())
    return record["place"] == question["gt_place"] and delta <= question["tolerance_s"]


def score(search: object, questions: list[dict], located_questions: list[dict]) -> dict:
    rows = []
    for question in questions:
        ids = [record["id"] for record in search(question)]
        gold = set(question["gold"])
        rows.append(
            {
                "question": question["q"],
                "kind": question["kind"],
                "top1": bool(ids) and ids[0] in gold if gold else None,
                "top3": bool(gold.intersection(ids[:3])) if gold else None,
                "returned": len(ids),
            }
        )
    for question in located_questions:
        hits = [located(record, question) for record in search(question)]
        rows.append(
            {"question": question["q"], "kind": "located", "top1": bool(hits and hits[0]), "top3": any(hits[:3])}
        )
    summary = {}
    for kind in ("keyword", "located", "paraphrase"):
        group = [row for row in rows if row["kind"] == kind]
        summary[kind] = {
            "questions": len(group),
            "top1": sum(row["top1"] for row in group) / len(group),
            "top3": sum(row["top3"] for row in group) / len(group),
        }
    unanswerable = [row for row in rows if row["kind"] == "unanswerable"]
    summary["unanswerable"] = {
        "questions": len(unanswerable),
        "returned_nothing": sum(row["returned"] == 0 for row in unanswerable) / len(unanswerable),
    }
    return {"summary": summary, "results": rows}


GATE = 0.7  # of the questions, the right record among the first three


def own(vault_root: Path, questions_file: Path) -> None:
    """Score questions written about a real vault (see the module's docstring)."""
    from ingest.config import Settings

    settings = Settings()
    vault = Vault(vault_root)
    store = LifeStore(vault, default_embedder(settings.vector_search, settings.embedding_model), zone=settings.timezone)
    asked = [json.loads(line) for line in questions_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    ranks = []
    for question in asked:
        found = retrieve(store, question["q"], day=question.get("date"), limit=10)
        phrase = question["has"].casefold()
        rank = next((n for n, record in enumerate(found, 1) if phrase in (record.get("text") or "").casefold()), None)
        ranks.append(rank)
        print(f"{'-' if rank is None else rank:>2}  {question['q']}")
    top = {k: sum(r is not None and r <= k for r in ranks) / len(ranks) for k in (1, 3, 10)}
    verdict = "met" if top[3] >= GATE else "not met"
    print(f"{len(ranks)} questions: first {top[1]:.0%}, in the first three {top[3]:.0%}, in ten {top[10]:.0%}; "
          f"the gate ({GATE:.0%} in the first three) is {verdict}")  # fmt: skip


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", type=Path, help="Your vault, to score your own questions on")
    parser.add_argument("--questions", type=Path, help="Your questions, one JSON object a line")
    args = parser.parse_args()
    if args.vault or args.questions:
        if not (args.vault and args.questions):
            parser.error("--vault and --questions go together")
        return own(args.vault, args.questions)
    questions = lines("questions_meaning.jsonl")
    located_questions = lines("questions.jsonl")
    with tempfile.TemporaryDirectory(prefix="daycube-eval-") as directory:
        vault = Vault(Path(directory) / "vault")
        vault.initialize()
        vault.seed()
        embedder = default_embedder(True)
        stores = {"keyword": LifeStore(vault)}
        if embedder is not None:
            combined = LifeStore(vault, embedder, path=vault.root / "vectors.sqlite")
            combined.ensure()
            stores["combined"] = combined

            def meaning(question: dict) -> list[dict]:
                near = [
                    unit
                    for unit, similarity in combined.nearest(question["q"], day=question["date"], k=10)
                    if similarity >= MIN_SIMILARITY
                ]
                rows = combined.rows(near)  # keyed by unit id; `near` keeps the similarity order
                return [{**rows[unit], "id": rows[unit]["evidence_id"], "iso_time": rows[unit]["t"]} for unit in near]

        report = {
            "kind": "synthetic-fixture-only",
            "model": embedder.name if embedder else None,
            "min_similarity": MIN_SIMILARITY,
            "methods": {},
        }
        for name, store in stores.items():
            report["methods"][name] = score(
                lambda q, store=store: retrieve(store, q["q"], day=q["date"]), questions, located_questions
            )
        if embedder is not None:
            report["methods"]["meaning"] = score(meaning, questions, located_questions)
    atomic_text(ROOT / "results/retrieval.json", json.dumps(report, indent=2) + "\n")
    print(json.dumps({name: method["summary"] for name, method in report["methods"].items()}, indent=2))


if __name__ == "__main__":
    main()

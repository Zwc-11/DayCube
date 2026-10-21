"""Small deterministic fixture check. This is not the planned human recall experiment."""
import json
from pathlib import Path

from ingest.ask.retrieve import retrieve
from ingest.config import Settings
from ingest.time import instant
from ingest.vault import Vault, atomic_text


def main():
    root = Path(__file__).resolve().parent
    questions = [json.loads(line) for line in (root / "questions.jsonl").read_text().splitlines() if line]
    vault = Vault(Settings().vault_root)
    rows = []
    for question in questions:
        results = retrieve(vault, question["date"], question["q"])
        hits = [record["place"] == question["gt_place"] and
                abs((instant(record["iso_time"]) - instant(question["gt_t"])).total_seconds()) <= question["tolerance_s"]
                for record in results]
        rows.append({"question": question["q"], "top1": bool(hits and hits[0]), "top3": any(hits[:3])})
    report = {"kind": "synthetic-fixture-only", "questions": len(rows),
              "top1": sum(row["top1"] for row in rows) / len(rows),
              "top3": sum(row["top3"] for row in rows) / len(rows), "results": rows}
    atomic_text(root / "results/retrieval.json", json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

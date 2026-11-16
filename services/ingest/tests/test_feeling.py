"""How the day felt (ingest/feeling.py): reports kept as a capture log, shown beside their
evidence, and a personal model that is dropped unless it beats the person's own average."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ingest import feeling
from ingest.api import create_app
from ingest.config import Settings
from ingest.review import timeline
from ingest.vault import Vault, read_lines
from test_day_review import DAY, ZONE, at, golden, log, moment


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    empty = Vault(tmp_path / "vault")
    empty.initialize()
    return empty


def test_a_report_is_kept_with_the_days_capture(vault: Vault) -> None:
    kept = feeling.report(vault, moment("09:50"), 4, 2, "  stuck on\n the timeline bug  ")
    assert kept == {"id": "fe-20261027095000", "t": at("09:50:00"), "kind": "feeling", "energy": 4, "mood": 2,
                    "note": "stuck on the timeline bug"}  # fmt: skip
    feeling.report(vault, moment("08:10"), 3, 3)
    assert [r["t"][11:16] for r in feeling.reports(vault, DAY)] == ["08:10", "09:50"]  # in time order
    assert read_lines(vault.root / "capture" / DAY / "feeling.jsonl")[0] == kept
    assert feeling.reports(vault, "2026-10-28") == []
    assert len(feeling.report(vault, moment("10:00"), 1, 5, "x" * 400)["note"]) == 280
    for energy, mood in ((0, 3), (3, 6)):
        with pytest.raises(ValueError, match="from 1 to 5"):
            feeling.report(vault, moment("11:00"), energy, mood)
    with pytest.raises(ValueError, match="UTC offset"):
        feeling.report(vault, datetime(2026, 10, 27, 11, 0), 3, 3)


def test_a_reports_evidence_is_what_was_recorded_in_the_ten_minutes_before(vault: Vault) -> None:
    golden(vault)
    typing = [
        {
            "t": (moment("09:35") + timedelta(seconds=10 * n)).isoformat(),
            "s": 10,
            "keys": 30,
            "clicks": 1,
            "move_px": 500,
        }
        for n in range(90)
    ]  # 09:35 to 09:50; the report's ten minutes hold 60 of the 90 buckets
    log(vault, "inputs.jsonl", typing)
    timeline.write(vault, DAY, ZONE)
    assert feeling.evidence(vault, moment("09:50"), ZONE) == {
        "keys_per_min": 180.0, "clicks_per_min": 6.0, "move_px_per_min": 3000.0, "switches_per_min": 0.1,
        "doing": "coding", "part": "morning",
    }  # fmt: skip
    away = feeling.evidence(vault, moment("11:00"), ZONE)  # idle since 10:50
    assert away["doing"] == "away" and away["keys_per_min"] == 0.0
    late = feeling.evidence(vault, moment("23:30"), ZONE)
    assert late["doing"] == "nothing recorded" and late["part"] == "night"
    assert [feeling.part_of_day(moment(c)) for c in ("04:59", "05:00", "12:00", "18:00", "23:00")] == [
        "night", "morning", "afternoon", "evening", "night",
    ]  # fmt: skip


def rows(n: int, *, signal: bool, seed: int = 1) -> list[dict]:
    """``n`` reports over 20 days. With ``signal``, mood follows how fast the person was typing;
    energy never follows anything recorded."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        keys = rng.choice([0, 5, 20, 60, 150, 300])
        mood = 1 + round(4 * keys / 300 + rng.gauss(0, 0.4)) if signal else rng.randint(1, 5)
        out.append({
            "day": f"2026-10-{1 + i % 20:02d}", "part": rng.choice(["morning", "afternoon", "evening"]),
            "doing": rng.choice(["coding", "reading"]), "keys_per_min": float(keys), "clicks_per_min": rng.random() * 5,
            "move_px_per_min": rng.random() * 4000, "switches_per_min": rng.random(), "energy": rng.randint(1, 5),
            "mood": min(max(mood, 1), 5),
        })  # fmt: skip
    return out


def test_the_model_is_kept_only_when_it_beats_the_persons_own_average() -> None:
    assert feeling.judge(rows(99, signal=True)) == {
        "reports": 99,
        "enough": 100,
        "verdict": "collecting",
        "targets": {},
    }
    judged = feeling.judge(rows(160, signal=True))
    mood, energy = judged["targets"]["mood"], judged["targets"]["energy"]
    assert judged["verdict"] == "kept" and mood["kept"] and not energy["kept"]
    assert mood["model_error"] < 0.6 * mood["usual_error"]  # typing speed says a lot about this person's mood
    assert energy["model_error"] > 0.95 * energy["usual_error"]  # and nothing about their energy
    noise = feeling.judge(rows(160, signal=False))
    assert noise["verdict"] == "dropped" and not any(t["kept"] for t in noise["targets"].values())


def test_the_api_takes_reports_and_shows_them_with_their_evidence(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ingest.api.deps

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return moment("09:50").astimezone(tz)

    monkeypatch.setattr(ingest.api.deps, "datetime", Clock)
    golden(vault)
    timeline.write(vault, DAY, ZONE)
    settings = Settings(vault_root=vault.root, vector_search=False, timezone=ZONE, _env_file=None)
    api = TestClient(create_app(settings), base_url="http://127.0.0.1")
    body = {"energy": 4, "mood": 5, "note": "it works"}
    assert api.post("/feeling", json=body).status_code == 401
    api.headers["Authorization"] = f"Bearer {vault.token}"
    made = api.post("/feeling", json=body).json()
    assert made["t"] == at("09:50:00") and made["evidence"]["doing"] == "coding" and made["note"] == "it works"
    earlier = api.post("/feeling", json={"energy": 2, "mood": 2, "t": "2026-10-27T12:10:00+00:00"}).json()
    assert earlier["t"] == at("08:10:00")  # kept in the vault's own time
    assert api.post("/feeling", json={"energy": 2, "mood": 2, "t": at("10:00:00")}).status_code == 400  # not yet
    assert api.post("/feeling", json={"energy": 9, "mood": 2}).status_code == 422
    day = api.get(f"/day/{DAY}/feeling").json()
    assert [(r["t"][11:16], r["energy"], r["evidence"]["part"]) for r in day["reports"]] == [
        ("08:10", 2, "morning"),
        ("09:50", 4, "morning"),
    ]
    assert api.get("/feeling/model").json() == {"reports": 2, "enough": 100, "verdict": "collecting", "targets": {}}
    span = {"t0": at("09:45:00"), "t1": at("09:55:00")}
    assert api.request("DELETE", f"/day/{DAY}/hours", json=span).json()["removed"]["capture"] == 1
    assert [r["t"][11:16] for r in api.get(f"/day/{DAY}/feeling").json()["reports"]] == ["08:10"]

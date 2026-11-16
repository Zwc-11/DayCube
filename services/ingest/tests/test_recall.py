"""Remembering the day (ingest/recall): a day cut into episodes with their signals, the morning's
questions, cue cards, the log of recalls, the weights fitted to answers, and deleting a span."""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ingest.api import create_app
from ingest.config import Settings
from ingest.recall import episodes, history, today
from ingest.recall.strength import MIN_ANSWERS, Signals, Weights, initial, replay
from ingest.review import timeline
from ingest.store import LifeStore
from ingest.vault import Vault, read_lines
from test_day_review import DAY, TZ, ZONE, at, golden, log, moment

TODAY = "2026-11-10"
NOW = datetime.fromisoformat(f"{TODAY}T08:00:00").replace(tzinfo=TZ)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    empty = Vault(tmp_path / "vault")
    empty.initialize()
    return empty


def segment(start: str, end: str, where: str = "desk", **more: object) -> dict:
    return {"t": at(start), "until": at(end), "where": where, "doing": where, "category": None, "project": None,
            "talk": [], "switches": 0, **more}  # fmt: skip


def episode(day: str, clock: str, minutes: float = 30.0, kind: str = "desk", **signals: float) -> dict:
    """An episode as episodes.json keeps it, starting at ``clock`` of ``day``."""
    t0 = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=TZ)
    return {
        "id": f"ep_{t0:%Y%m%d_%H%M}", "t": t0.isoformat(), "until": (t0 + timedelta(minutes=minutes)).isoformat(),
        "kind": kind, "where": "desk", "title": f"what happened at {clock}", "category": "coding", "project": None,
        "talk": [], "minutes": minutes, "keys": ["app:code.exe"], "evidence": [], "fidelity": "known",
        "signals": {"attention": 0.0, "feeling": 0.0, "novelty": 0.0, "importance": 0.0, **signals},
    }  # fmt: skip


def keep(vault: Vault, day: str, *found: dict) -> None:
    path = vault.day_path(day, must_exist=False) / episodes.FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    vault.write_json(path, {"date": day, "episodes": list(found)})


def back(days: int) -> str:
    return (date.fromisoformat(TODAY) - timedelta(days=days)).isoformat()


# -- episodes -----------------------------------------------------------------------------------


def test_a_day_is_cut_where_what_it_was_about_changed() -> None:
    cut = episodes.cut([
        segment("09:00", "09:40", project="DayCube", category="coding"),
        segment("09:40", "09:42", category="social"),  # a glance elsewhere: inside the episode
        segment("09:42", "10:30", project="DayCube", category="coding"),
        segment("10:30", "10:34", "away"),  # four minutes away: the same episode goes on
        segment("10:34", "11:00", project="DayCube", category="coding"),
        segment("11:00", "11:40", "away"),
        segment("11:40", "12:25", "place:cafe"),
        segment("12:30", "12:32", category="email"),  # two minutes: not an episode
        segment("13:00", "13:30", talk=["m1"]),
        segment("13:30", "14:10", category="coding"),  # no project: the kind of activity
        segment("14:10", "14:50", category="reading"),
        segment("14:50", "15:30", category="private"),  # a page the extension does not record: nothing to recall
    ])  # fmt: skip
    assert [(g[0]["t"][11:16], g[-1]["until"][11:16], len(g)) for g in cut] == [
        ("09:00", "11:00", 4),
        ("11:40", "12:25", 1),
        ("13:00", "13:30", 1),
        ("13:30", "14:10", 1),
        ("14:10", "14:50", 1),
    ]


def signals(units: list[dict] = (), minutes: float = 60.0, active_s: float = 0.0, switches: int = 0,
            reports: list[dict] = (), keys: set[str] = frozenset(), known: set[str] | None = None) -> Signals:  # fmt: skip
    t0 = moment("09:00").timestamp()
    return episodes._signals(list(units), minutes, active_s, switches, list(reports), set(keys), known, t0, t0 + 3600)


def test_attention_is_reading_or_working_and_switching_lowers_it() -> None:
    read = [{"type": "web.read", "text": f"line {n}"} for n in range(20)]
    assert signals().attention == 0.0
    assert signals(read).attention == pytest.approx(0.632, abs=0.001)
    assert signals(read + read[:5]).attention > signals(read + [{"type": "web.read", "text": "new"}] * 1).attention
    assert signals(active_s=1800).attention == 0.5  # typing or clicking half the time
    assert signals(read, active_s=1800).attention == signals(read).attention  # the higher of the two
    assert signals(read, switches=120).attention == pytest.approx(0.632 * 0.5, abs=0.001)  # two a minute
    assert signals(read, switches=600).attention == pytest.approx(0.632 * 0.5, abs=0.001)  # never below half


def test_feeling_novelty_and_importance_come_from_what_was_recorded() -> None:
    def felt(clock: str, energy: int, mood: int) -> dict:
        return {"t": at(clock), "energy": energy, "mood": mood}

    assert signals(reports=[felt("09:30", 3, 3)]).feeling == 0.0
    assert signals(reports=[felt("09:30", 3, 5)]).feeling == 1.0
    assert signals(reports=[felt("10:30", 2, 3)]).feeling == 0.5  # half an hour after it ended still speaks
    assert signals(reports=[felt("12:00", 1, 1)]).feeling == 0.0  # two hours after does not
    assert signals(keys={"app:a", "site:b"}, known={"app:a"}).novelty == 0.5
    assert signals(keys={"app:a", "site:b"}, known=None).novelty == 0.0  # nothing to judge against yet
    search, made = {"type": "web.search", "text": "q"}, {"type": "moment.photo", "text": "p"}
    assert signals([search]).importance == pytest.approx(0.283, abs=0.001)
    assert signals([made]).importance > signals([search, search]).importance > signals([search]).importance


def test_a_recorded_day_becomes_episodes_and_is_written_once(vault: Vault) -> None:
    golden(vault)
    typing = [{"t": (moment("09:00") + timedelta(seconds=10 * n)).isoformat(), "s": 10, "keys": 12} for n in range(270)]
    log(vault, "inputs.jsonl", typing)  # 45 of the 90 minutes of coding
    log(vault, "feeling.jsonl", [{"t": at("11:30:00"), "kind": "feeling", "energy": 5, "mood": 4}])
    timeline.write(vault, DAY, ZONE)
    store = LifeStore(vault, zone=ZONE)
    found = episodes.write(vault, store, DAY, ZONE)
    assert [(e["id"], e["kind"], e["minutes"], e["project"], e["category"]) for e in found] == [
        ("ep_20261027_0900", "desk", 90.0, "DayCube", "coding"),
        ("ep_20261027_1030", "desk", 20.0, None, "social"),
        ("ep_20261027_1120", "desk", 40.0, None, "ai"),
    ]
    coding, social, claude = found
    assert coding["title"].startswith("DayCube: Visual Studio Code") and social["title"] == "social: Home / X (x.com)"
    assert coding["signals"] == {"attention": 0.499, "feeling": 0.0, "novelty": 0.0, "importance": 0.0}
    assert social["signals"]["importance"] == 0.283 and social["signals"]["attention"] > 0  # a search; a line read
    assert (
        claude["signals"]["feeling"] == 1.0 and social["signals"]["feeling"] == 1.0
    )  # the report at 11:30 reaches both
    assert social["keys"] == ["site:x.com"] and coding["evidence"]
    assert (
        social["highlights"]
        == [
            {"kind": "search", "text": "Searched google: rust ownership"},
            {"kind": "read", "text": "Read in Home / X (x.com): Ownership moves a value and borrowing lends it"},
        ]
        and coding["highlights"] == []
    )
    path = vault.day_path(DAY, must_exist=False) / episodes.FILE
    before = path.stat().st_mtime_ns
    assert episodes.write(vault, store, DAY, ZONE) == found == episodes.read(vault, DAY)
    assert path.stat().st_mtime_ns == before  # unchanged: not written again


def test_something_is_new_only_against_a_week_of_days_before(vault: Vault) -> None:
    golden(vault)
    timeline.write(vault, DAY, ZONE)
    store = LifeStore(vault, zone=ZONE)
    first = date.fromisoformat(DAY)
    for n in range(1, 7):
        keep(vault, (first - timedelta(days=n)).isoformat(), episode(DAY, "09:00"))
    assert {e["signals"]["novelty"] for e in episodes.build(vault, store, DAY, ZONE)} == {0.0}  # six days: too few
    keep(vault, (first - timedelta(days=7)).isoformat(), episode(DAY, "09:00"))
    assert [e["signals"]["novelty"] for e in episodes.build(vault, store, DAY, ZONE)] == [
        0.0,
        1.0,
        1.0,
    ]  # VS Code is known


# -- recall -------------------------------------------------------------------------------------


def remembered(vault: Vault) -> None:
    """Episodes one, three and seven days back, and two a fortnight back that no question reaches."""
    keep(
        vault,
        back(1),
        episode(back(1), "09:00", importance=0.9),
        episode(back(1), "14:00", attention=0.2),
        episode(back(1), "16:00", minutes=5.0, importance=1.0),
    )  # the last: too short to ask about
    keep(vault, back(3), episode(back(3), "10:00", kind="talk", feeling=0.8))
    keep(vault, back(7), episode(back(7), "12:00", kind="place", novelty=1.0))
    keep(vault, back(14), episode(back(14), "11:00", importance=0.6), episode(back(14), "15:00", feeling=0.5),
         episode(back(14), "17:00", attention=0.9))  # fmt: skip


def test_three_questions_are_asked_from_the_spaced_days_and_kept_for_the_day(vault: Vault) -> None:
    remembered(vault)
    asked = today.questions(vault, TODAY, NOW)
    assert [(q["id"], q["day"], q["episode"]) for q in asked["questions"]] == [
        ("q_20261110_1", back(1), "ep_20261109_0900"),  # what mattered most that day
        ("q_20261110_2", back(3), "ep_20261107_1000"),
        ("q_20261110_3", back(7), "ep_20261103_1200"),
    ]
    first, talk, place = asked["questions"]
    assert first["cue"] == "Monday 9 November, 09:00: about 30 minutes at your desk. What were you doing?"
    assert first["answer"] == "what happened at 09:00" and 0 < first["left"] < 1
    assert first["highlights"] == [] and first["kind"] == "desk"  # episodes written before highlights were kept
    assert "a conversation of about 30 minutes" in talk["cue"] and "away from your desk" in place["cue"]
    keep(vault, back(1), episode(back(1), "20:00", importance=1.0))  # the day changes after the questions were chosen
    assert today.questions(vault, TODAY, NOW) == asked
    assert today.questions(vault, back(30), NOW - timedelta(days=30)) == {
        "date": back(30), "questions": [], "answers": {}
    }  # fmt: skip
    assert not (vault.root / "recall" / "questions" / f"{back(30)}.json").exists()  # nothing to keep


def test_an_answer_is_logged_once_and_changes_the_strength(vault: Vault) -> None:
    remembered(vault)
    first, second, _ = today.questions(vault, TODAY, NOW)["questions"]
    found = episodes.read(vault, back(1))
    before = history.with_strength(vault, found, NOW)[0]["strength"]
    assert before == {"stability_days": pytest.approx(initial(Signals(importance=0.9)), abs=0.01),
                      "left": first["left"], "recalls": 0}  # fmt: skip
    assert today.answer(vault, TODAY, first["id"], "remembered", NOW)["answers"] == {first["id"]: "remembered"}
    today.answer(vault, TODAY, first["id"], "forgot", NOW)  # the first verdict counts
    today.answer(vault, TODAY, second["id"], "forgot", NOW)
    kept = read_lines(vault.root / "recall" / "log.jsonl")
    assert [(r["episode"], r["day"], r["outcome"]) for r in kept] == [
        (first["episode"], back(1), "remembered"),
        (second["episode"], back(3), "forgot"),
    ]
    after = history.with_strength(vault, found, NOW + timedelta(hours=1))[0]["strength"]
    assert after["recalls"] == 1 and after["stability_days"] > before["stability_days"] and after["left"] > 0.99
    talk = episodes.read(vault, back(3))
    weaker = history.with_strength(vault, talk, NOW + timedelta(hours=1))[0]["strength"]
    assert weaker["stability_days"] == pytest.approx(initial(Signals(feeling=0.8)) / 2, abs=0.01)
    with pytest.raises(FileNotFoundError):
        today.answer(vault, TODAY, "q_20261110_9", "remembered", NOW)
    with pytest.raises(ValueError, match="one of"):
        today.answer(vault, TODAY, first["id"], "opened", NOW)


def test_cards_show_fading_episodes_that_mattered_and_opening_one_is_a_recall(vault: Vault) -> None:
    remembered(vault)
    shown = today.cards(vault, TODAY, NOW)
    # Not the ones asked today or the short one, nor what held attention but did not matter.
    assert [(c["episode"], c["day"]) for c in shown] == [("ep_20261027_1500", back(14)), ("ep_20261027_1100", back(14))]
    assert shown[0]["title"] == "what happened at 15:00" and shown[0]["when"] == "Tuesday 27 October, 15:00"
    assert all(c["left"] < today.CARD_BELOW for c in shown)
    today.opened(vault, back(14), "ep_20261027_1500", NOW)
    again = today.cards(vault, TODAY, NOW + timedelta(minutes=1))
    assert [c["episode"] for c in again] == ["ep_20261027_1100"]  # just seen: nothing of it has faded
    assert history.status(vault)["recalls"] == {"remembered": 0, "partly": 0, "opened": 1, "forgot": 0}
    with pytest.raises(FileNotFoundError):
        today.opened(vault, back(14), "ep_20261027_0000", NOW)
    keep(vault, back(20), *[episode(back(20), f"{h:02d}:00", importance=0.9) for h in range(8, 14)])
    assert len(today.cards(vault, TODAY, NOW)) == today.CARDS


def test_weights_are_fitted_to_the_answers_and_kept_only_when_better(vault: Vault) -> None:
    truth, rng = Weights(s=0.4, a=2.5, d=0.2), random.Random(3)
    start = date.fromisoformat("2026-01-01")
    lines = []
    for n in range(240):
        day = (start + timedelta(days=n // 8)).isoformat()
        found = episode(day, f"{8 + n % 8:02d}:00", attention=rng.random(), importance=rng.random())
        keep(vault, day, *(episodes.read(vault, day) or []), found)
        made = datetime.fromisoformat(found["until"])
        asked = made + timedelta(days=rng.choice([1, 3, 7, 21, 60]))
        chance = replay(initial(Signals(**found["signals"]), truth), made.timestamp(), [], asked.timestamp(), truth)
        outcome = "remembered" if rng.random() < chance.retention else "forgot"
        lines.append({"t": asked.isoformat(), "episode": found["id"], "day": day, "outcome": outcome})
    lines.sort(key=lambda r: r["t"])
    folder = vault.root / "recall"
    folder.mkdir()
    vault.append_lines(folder / "log.jsonl", lines[: MIN_ANSWERS - 1])
    assert history.refit(vault) == Weights() and not history.status(vault)["fitted"]  # too few to fit
    vault.append_lines(folder / "log.jsonl", lines[MIN_ANSWERS - 1 :])
    fitted = history.refit(vault)
    assert fitted != Weights() and fitted.a > 1.5 and fitted.s < 0.8  # towards this person
    assert history.weights(vault) == fitted and history.refit(vault) == fitted
    status = history.status(vault)
    assert status["fitted"] and status["graded"] == 240 and status["weights"]["a"] == fitted.a
    assert json.loads((folder / "weights.json").read_text())["answers"] == 240


def test_the_nightly_round_writes_finished_days_and_fits(vault: Vault) -> None:
    golden(vault)
    timeline.write(vault, DAY, ZONE)
    store = LifeStore(vault, zone=ZONE)
    night = moment("04:10") + timedelta(days=1)
    assert today.night(vault, store, ZONE, moment("23:00")) == []  # the day is not over
    assert today.night(vault, store, ZONE, night) == [DAY]
    assert len(episodes.read(vault, DAY)) == 3
    assert today.night(vault, store, ZONE, night + timedelta(minutes=10)) == []  # nothing changed
    # A day from before recall existed is written the first night, then left as it is.
    path = vault.day_path(DAY, must_exist=False) / episodes.FILE
    path.unlink()
    assert today.night(vault, store, ZONE, night + timedelta(days=40)) == [DAY]
    vault.write_json(path, {"date": DAY, "episodes": []})
    assert today.night(vault, store, ZONE, night + timedelta(days=41)) == [] and episodes.read(vault, DAY) == []


# -- the API ------------------------------------------------------------------------------------


@pytest.fixture
def api(vault: Vault) -> TestClient:
    settings = Settings(vault_root=vault.root, vector_search=False, timezone=ZONE, _env_file=None)
    client = TestClient(create_app(settings), base_url="http://127.0.0.1")
    client.headers["Authorization"] = f"Bearer {vault.token}"
    return client


def test_the_api_serves_episodes_questions_cards_and_takes_answers(
    vault: Vault, api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ingest.api.deps

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return NOW.astimezone(tz)

    monkeypatch.setattr(ingest.api.deps, "datetime", Clock)
    remembered(vault)
    day = api.get(f"/day/{back(1)}/episodes").json()
    assert day["written"] and len(day["episodes"]) == 3 and day["episodes"][0]["strength"]["recalls"] == 0
    assert api.get(f"/day/{back(2)}/episodes").json() == {"date": back(2), "written": False, "episodes": []}
    asked = api.get("/recall/today").json()
    assert len(asked["questions"]) == 3 and len(asked["cards"]) == 2 and asked["answers"] == {}
    question = asked["questions"][0]["id"]
    body = {"question": question, "outcome": "partly"}
    assert api.post("/recall/answer", json=body, headers={"Authorization": ""}).status_code == 401
    assert api.post("/recall/answer", json=body).json()["answers"] == {question: "partly"}
    assert api.post("/recall/answer", json={"question": "q_x", "outcome": "forgot"}).status_code == 404
    assert api.post("/recall/answer", json={"question": question, "outcome": "opened"}).status_code == 422
    card = asked["cards"][0]
    opened = api.post("/recall/opened", json={"date": card["day"], "episode": card["episode"]})
    assert opened.status_code == 200 and opened.json()["outcome"] == "opened"
    assert api.post("/recall/opened", json={"date": card["day"], "episode": "ep_none"}).status_code == 404
    status = api.get("/recall").json()
    assert status["recalls"] == {"remembered": 0, "partly": 1, "opened": 1, "forgot": 0}
    assert status["graded"] == 1 and status["came_back"] == 0.5 and not status["fitted"]


def test_a_deleted_span_leaves_no_episode_or_question_that_quotes_it(vault: Vault, api: TestClient) -> None:
    golden(vault)
    timeline.write(vault, DAY, ZONE)
    episodes.write(vault, LifeStore(vault, zone=ZONE), DAY, ZONE)
    tomorrow = moment("08:00") + timedelta(days=1)
    asked = today.questions(vault, tomorrow.date().isoformat(), tomorrow)
    assert [q["answer"] for q in asked["questions"]] == [
        "DayCube: Visual Studio Code: timeline.py - DAYCUBE - Visual Studio Code"
    ]  # the longest stretch of the day
    today.answer(vault, asked["date"], asked["questions"][0]["id"], "remembered", tomorrow)
    span = {"t0": at("09:00:00"), "t1": at("10:30:00")}
    assert api.request("DELETE", f"/day/{DAY}/hours", json=span).status_code == 200
    left = episodes.read(vault, DAY)
    assert [(e["id"], e["minutes"]) for e in left] == [("ep_20261027_1030", 20.0), ("ep_20261027_1120", 40.0)]
    assert not list((vault.root / "recall" / "questions").glob("*.json"))
    made = [vault.day_path(DAY, must_exist=False) / name for name in (episodes.FILE, "timeline.jsonl")]
    kept = b"".join(p.read_bytes() for p in [*made, *(vault.root / "recall").rglob("*")] if p.is_file())
    assert b"timeline.py" not in kept and b"Visual Studio Code" not in kept
    assert len(read_lines(vault.root / "recall" / "log.jsonl")) == 1  # the log names the episode, quotes nothing


# -- on schedule and from the command line ------------------------------------------------------


def test_the_librarian_runs_the_nightly_round_once_a_review_day(vault: Vault) -> None:
    from capture.librarian import Librarian

    golden(vault)
    timeline.write(vault, DAY, ZONE)
    librarian = Librarian(vault.root, ZONE)
    assert librarian.step_recall(moment("23:50")) == []  # the day is not over; the round has run for it
    assert librarian.step_recall(moment("03:59", "2026-10-28")) == []
    assert librarian.step_recall(moment("04:05", "2026-10-28")) == [DAY]
    assert len(episodes.read(vault, DAY)) == 3
    (vault.day_path(DAY, must_exist=False) / episodes.FILE).unlink()
    assert librarian.step_recall(moment("04:15", "2026-10-28")) == []  # once a review day
    assert librarian.step_recall(moment("04:05", "2026-10-29")) == [DAY]


def test_the_command_writes_a_days_episodes_or_runs_the_round(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    from ingest import cli

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return moment("08:00", "2026-10-28").astimezone(tz)

    monkeypatch.setattr(cli, "datetime", Clock)
    golden(vault)
    settings = Settings(vault_root=vault.root, vector_search=False, timezone=ZONE, _env_file=None)
    parsed = cli.build_parser().parse_args(["recall", "--date", DAY])
    assert parsed.run is cli.recall
    out = cli.recall(parsed, vault, settings)  # writes the timeline it needs first
    assert [e["id"] for e in out["episodes"]] == ["ep_20261027_0900", "ep_20261027_1030", "ep_20261027_1120"]
    assert out["episodes"][0]["strength"]["left"] < 1
    (vault.day_path(DAY, must_exist=False) / episodes.FILE).unlink()
    out = cli.recall(cli.build_parser().parse_args(["recall"]), vault, settings)
    assert out["written"] == [DAY] and out["date"] == "2026-10-28" and out["cards"] == []
    assert [q["episode"] for q in out["questions"]] == ["ep_20261027_0900"] and out["status"]["graded"] == 0

"""The end-of-day review (ingest/review/metrics.py, summary.py): numbers made by code, words
checked against them, written on a schedule and on request, shown in the daily note and the API."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from ingest import contracts
from ingest.api import create_app
from ingest.config import Settings
from ingest.notes import Notes
from ingest.review import build, metrics
from ingest.review.summary import compose, read_review, template, validate, write_review
from ingest.store import LifeStore
from ingest.vault import Vault

ZONE = "America/Toronto"
TZ = ZoneInfo(ZONE)
DAY = "2026-10-27"
CODE = "timeline.py - DAYCUBE - Visual Studio Code"
X = {"tab": 4, "window": 1, "url": "https://x.com/home", "title": "Home / X"}


def at(clock: str, day: str = DAY) -> str:
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=TZ).isoformat()


def moment(clock: str, day: str = DAY) -> datetime:
    return datetime.fromisoformat(at(clock, day))


def log(vault: Vault, name: str, records: list[dict]) -> None:
    for n, record in enumerate(records):
        record.setdefault("id", f"{name[0]}{n}-{record['t'][11:19].replace(':', '')}-{record['t'][8:10]}")
        day = datetime.fromisoformat(record["t"]).astimezone(TZ).date().isoformat()
        folder = vault.root / "capture" / day
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / name).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")


def app(t: str, program: str, title: str) -> dict:
    return {"t": t, "kind": "window", "app": program, "title": title, "url": f"app://{program}/x"}


def golden(vault: Vault, day: str = DAY) -> None:
    """09:00 coding for 90 min (an 8 s glance at X at 09:30), 20 min on X with a search and a line
    read, away from 10:50 (idle) to 11:20, 40 min in Claude, locked at 12:00."""
    (vault.root / "projects.yaml").write_text("projects:\n  DayCube:\n    folders: ['E:\\DAYCUBE']\n", "utf-8")
    log(vault, "windows.jsonl", [
        app(at("09:00:00", day), "code.exe", CODE),
        {"t": at("09:30:00", day), "kind": "window", "app": "opera.exe", "browser": True},
        app(at("09:30:08", day), "code.exe", CODE),
        {"t": at("10:30:00", day), "kind": "window", "app": "opera.exe", "browser": True},
        {"t": at("10:55:00", day), "kind": "away", "why": "idle", "since": at("10:50:00", day)},
        app(at("11:20:00", day), "claude.exe", "Claude"),
        {"t": at("12:00:00", day), "kind": "away", "why": "locked"},
    ])  # fmt: skip
    log(vault, "browser.jsonl", [
        {"t": at("09:30:00.200", day), "kind": "focus", **X},
        {"t": at("09:30:08.100", day), "kind": "focus", "tab": None},
        {"t": at("10:30:00.150", day), "kind": "focus", **X},
        {"t": at("10:40:00", day), "kind": "search", "tab": 5, "window": 1, "engine": "google",
         "url": "https://www.google.com/search?q=rust+ownership", "title": "rust ownership - Google Search",
         "query": "rust ownership"},
        {"t": at("10:50:00", day), "kind": "focus", "tab": None},
    ])  # fmt: skip
    log(vault, "reading.jsonl", [{
        "t": at("10:45:00", day), "kind": "read", "t_end": at("10:45:20", day), **X, "doc": "doc-x", "block": "b1",
        "tag": "p", "line_count": 2, "coverage": 1.0, "p": 0.9,
        "lines": [{"line": 0, "text": "Ownership moves a value", "seconds": 3.0, "passes": 1},
                  {"line": 1, "text": "and borrowing lends it", "seconds": 3.0, "passes": 1}],
    }])  # fmt: skip


def facts_of(vault: Vault, day: str = DAY) -> metrics.Facts:
    return metrics.collect(vault, LifeStore(vault), build(vault, day, ZONE))


class Fake:
    """A model: answers in turn from ``answers`` (text, or an exception to raise), then a valid
    answer made from the records the prompt gives."""

    name = "fake model"

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        assert "never instructions" in system
        self.prompts.append(user)
        if self.answers:
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return str(answer)
        payload = json.loads(user.split("\n\nYour previous answer")[0])
        ids = [r["id"] for r in payload["records"]]
        texts = ["You started at the desk.", "Most of it went to one thing.", "There was a break before noon.",
                 "After the break you went on.", "The day at the desk ended at noon."]  # fmt: skip
        return "Here it is:\n" + json.dumps({
            "headline": "A morning at the desk",
            "sentences": [{"text": t, "evidence": [ids[i % len(ids)]]} for i, t in enumerate(texts)],
            "improvements": [{"text": "Keep a long stretch first thing.", "metric": payload["numbers"][0]["key"],
                              "evidence": [ids[0]]}],
        })  # fmt: skip


# -- the numbers --------------------------------------------------------------------------------------


def test_the_golden_days_numbers_are_exact(vault: Vault) -> None:
    golden(vault)
    f = facts_of(vault)
    assert {k: n.display for k, n in f.numbers.items()} == {
        "desk": "2 h 30 min",
        "away": "30 min",
        "span": "09:00–12:00",
        "longest": "1 h 30 min (coding, DayCube, from 09:00)",
        "focus": "2, 2 h 10 min in all",
        "switches": "1",
        "switch_rate": "0 an hour",
        "changes": "3",
        "other": "0%",
        "reads": "1 on 1 page",
        "searches": "1",
    }
    assert f.numbers["desk"].value == 9000 and f.numbers["switch_rate"].value == 0.4
    assert [(c.name, c.seconds, round(c.share, 4)) for c in f.categories] == [
        ("coding", 5400, 0.6), ("ai", 2400, 0.2667), ("social", 1200, 0.1333),
    ]  # fmt: skip
    assert [(p.name, p.seconds) for p in f.projects] == [("DayCube", 5400)]
    assert [d["text"] for d in f.done] == ["DayCube: 1 h 30 min (coding 1 h 30 min)"]
    assert [t["text"] for t in f.tomorrow] == ["Where the desk work stopped, at 12:00: ai, Claude"]
    assert not f.illustrative and f.digest == facts_of(vault).digest  # the same records, the same facts
    # Every number cites records the review carries: the timeline's segments and the day's units.
    for number in f.numbers.values():
        assert number.evidence and all(i in f.evidence for i in number.evidence)
    longest = f.evidence[f.numbers["longest"].evidence[0]]
    assert longest["kind"] == "timeline" and longest["t"] == at("09:00:00") and longest["until"] == at("10:30:00")
    assert longest["records"][0].startswith("capture:app:")
    assert f.evidence[f.numbers["searches"].evidence[0]]["text"] == "Searched google: rust ownership"


def test_the_template_says_the_numbers_and_passes_its_own_checks(vault: Vault) -> None:
    golden(vault)
    f = facts_of(vault)
    words = template(f)
    assert words.headline == "2 h 30 min at the desk, mostly coding; longest stretch 1 h 30 min"
    assert [s["text"] for s in words.sentences] == [
        "You were at the desk 2 h 30 min, from 09:00 to 12:00, and away from it 30 min.",
        "Most of it went to coding, 1 h 30 min (60%), then ai (40 min) and social (20 min).",
        "By project: DayCube 1 h 30 min.",
        "The longest stretch on one thing was 1 h 30 min of coding on DayCube, from 09:00.",
        "2 stretches lasted 25 min or more, 2 h 10 min in all.",
        "Something else came to front for under 20 s once.",
        "You read 1 passage line by line on 1 page and searched once.",
    ]
    assert words.improvements == []  # nothing on this day crosses a threshold
    answer = {"headline": words.headline, "sentences": words.sentences, "improvements": words.improvements}
    assert validate(answer, f) == []


def test_the_words_say_what_the_day_boundary_and_unknowns_are(vault: Vault) -> None:
    """At the desk from the night before and until 04:00; an unrecorded page; a 1-minute project."""
    still = [{"t": (moment("05:02:00") + timedelta(minutes=5 * k)).isoformat(), "kind": "still"} for k in range(287)]
    log(vault, "windows.jsonl", [
        {"t": at("03:00:00"), "kind": "window", "app": "opera.exe", "browser": True},  # a page not recorded
        app(at("05:00:00"), "code.exe", "x.py - tiny - Visual Studio Code"),
        app(at("05:01:00"), "claude.exe", "Claude"),
        *still,  # the reader still recording, every 5 minutes, through the night
        {"t": at("05:00:00", "2026-10-28"), "kind": "away", "why": "locked"},
    ])  # fmt: skip
    f = facts_of(vault)
    words = template(f)
    assert words.sentences[0]["text"].startswith(
        "You were at the desk 24 h, from 04:00, still at it from the night before, to 04:00, when the day ends"
    )
    assert "pages not recorded" in words.sentences[1]["text"] and not any("By project" in s["text"] for s in words.sentences)
    answer = {"headline": words.headline, "sentences": words.sentences, "improvements": words.improvements}
    assert validate(answer, f) == []


def test_improvements_are_tied_to_numbers(vault: Vault) -> None:
    """A scattered evening: an hour on X with a glance every minute, past midnight."""
    log(vault, "windows.jsonl", [{"t": at("23:00:00"), "kind": "window", "app": "opera.exe", "browser": True}])
    records = [{"t": at("23:00:00.100"), "kind": "focus", **X}]
    window = []
    for minute in range(90):
        t = moment("23:01:00") + timedelta(minutes=minute)
        window += [app(t.isoformat(), "weixin.exe", "微信"),
                   {"t": (t + timedelta(seconds=5)).isoformat(), "kind": "window", "app": "opera.exe", "browser": True}]  # fmt: skip
        records.append({"t": (t + timedelta(seconds=5, milliseconds=100)).isoformat(), "kind": "focus", **X})
    window.append({"t": at("00:31:00", "2026-10-28"), "kind": "away", "why": "locked"})
    log(vault, "windows.jsonl", window)
    log(vault, "browser.jsonl", records)
    f = facts_of(vault)
    words = template(f)
    # One 91-minute stretch on X (the glances at WeChat are switches): social, the glances, the late hour.
    assert [i["metric"] for i in words.improvements] == ["category:social", "switch_rate", "late"]
    # 90 glances in the 91 minutes from 23:00 to 00:31; 31 of those minutes after midnight.
    assert f.numbers["switch_rate"].display == "59 an hour" and f.numbers["late"].display == "31 min"
    for item in words.improvements:
        assert item["evidence"] and all(i in f.evidence for i in item["evidence"])
    answer = {"headline": words.headline, "sentences": words.sentences, "improvements": words.improvements}
    assert validate(answer, f) == []


# -- the validator ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fault", "problem"),
    [
        (lambda a, f: a["sentences"][0].update(text="You were at the desk 7 h 13 min."), "the number 7 is not in the facts"),
        (lambda a, f: a["sentences"][0].update(evidence=["capture:app:made-up"]),
         "cites capture:app:made-up, which is not one of the day's records"),
        (lambda a, f: (f.background.update({"capture:read:old": {"t": None, "until": None, "day": "2026-10-20",
                                                                 "kind": "web.read", "text": "x", "fidelity": "known"}}),
                       a["sentences"][0].update(evidence=["capture:read:old"])), "a record of another day"),
        (lambda a, f: a["sentences"][1].update(text="Most of it went to coding, then to Netflix."),
         "“Netflix” is not in the records it cites"),
        (lambda a, f: a["sentences"][1].update(text="You read about it on example.org."),
         "example.org is not in the records it cites"),
        (lambda a, f: a["sentences"][1].update(text="You said “I will finish it tomorrow” to yourself."),
         "the quotation “I will finish it tomorrow” is not in the records it cites"),
        (lambda a, f: a["sentences"][2].update(text="See [[Daily/2026-10-27]] for more."), "Markdown, a link or markup"),
        (lambda a, f: a["sentences"][2].update(text="Read <b>this</b> now."), "Markdown, a link or markup"),
        (lambda a, f: a["sentences"][3].update(evidence=[]), "sentence 4: cites no record"),
        (lambda a, f: a.update(sentences=a["sentences"][:2]), "sentences: 2, not 5 to 8"),
        (lambda a, f: a["sentences"].append(dict(a["sentences"][0])), "said twice"),
        (lambda a, f: a.update(headline="A" * 130), "headline: longer than 120 characters"),
        (lambda a, f: a.update(improvements=[{"text": "Be happier.", "metric": "happiness",
                                              "evidence": a["sentences"][0]["evidence"]}]),
         "is tied to 'happiness', which is not one of the numbers"),
    ],
)  # fmt: skip
def test_the_validator_catches_injected_faults(vault: Vault, fault: object, problem: str) -> None:
    golden(vault)
    f = facts_of(vault)
    words = template(f)
    answer = json.loads(json.dumps({"headline": words.headline, "sentences": words.sentences, "improvements": []}))
    assert validate(answer, f) == []
    fault(answer, f)  # type: ignore[operator]
    found = validate(answer, f)
    assert any(problem in p for p in found), found


def test_a_rejected_answer_is_retried_once_then_the_template_writes(vault: Vault) -> None:
    golden(vault)
    f = facts_of(vault)
    bad = json.dumps({"headline": "You worked 17 hours", "sentences": [{"text": "Fine.", "evidence": ["nope"]}]})
    # Rejected, then right: the model's words, with why the first answer was not used.
    model = Fake(bad)
    words, writer = compose(f, model, "final")
    assert writer["name"] == "fake model" and writer["attempts"] == 2 and len(writer["rejected"]) == 1
    assert "the number 17 is not in the facts" in writer["rejected"][0]
    assert "Your previous answer was rejected" in model.prompts[1] and "cites nope" in model.prompts[1]
    assert words.headline == "A morning at the desk" and len(words.sentences) == 5
    # Rejected twice, or the call failing: the template's words.
    for model in (Fake(bad, bad), Fake(TimeoutError("no network"), "not json at all")):
        words, writer = compose(f, model, "final")
        assert writer["name"] == "template" and len(writer["rejected"]) == 2
        assert words.headline == template(f).headline
    # What the model is shown: the day's numbers and only records it may cite.
    payload = json.loads(Fake().prompts or model.prompts[0])
    assert {r["id"] for r in payload["records"]} <= set(f.evidence)
    assert [n["key"] for n in payload["numbers"]] == list(f.numbers)


# -- writing ------------------------------------------------------------------------------------------


def test_the_same_facts_are_not_written_twice(vault: Vault) -> None:
    golden(vault)
    store = LifeStore(vault)
    model = Fake()
    after = moment("09:00:00", "2026-10-28")
    first = write_review(vault, store, DAY, ZONE, now=after, model=model)
    assert first["written"] and first["review"]["kind"] == "final" and len(model.prompts) == 1
    contracts.validate("review", read_review(vault, DAY))
    again = write_review(vault, store, DAY, ZONE, now=after + timedelta(minutes=10), model=model)
    assert not again["written"] and again["why"] == "unchanged" and len(model.prompts) == 1
    # A draft becomes the final without being written again when its facts did not change.
    (vault.root / DAY / "review.json").unlink()
    draft = write_review(vault, store, DAY, ZONE, now=after, model=model, kind="draft")
    final = write_review(vault, store, DAY, ZONE, now=after, model=model)
    assert draft["review"]["kind"] == "draft" and final["review"]["kind"] == "final"
    assert final["why"] == "the draft's facts did not change: now the final" and len(model.prompts) == 2
    assert final["review"]["sentences"] == draft["review"]["sentences"]
    # New records: new facts, written again.
    log(vault, "browser.jsonl", [{"t": at("11:30:00"), "kind": "search", "tab": 5, "window": 1, "engine": "google",
                                  "url": "https://www.google.com/search?q=b", "title": "b", "query": "borrow checker"}])  # fmt: skip
    revised = write_review(vault, store, DAY, ZONE, now=after, model=model)
    assert revised["why"] == "revised: its facts changed" and len(model.prompts) == 3
    assert revised["review"]["facts"] != final["review"]["facts"]
    nothing = write_review(vault, store, "2026-10-29", ZONE, now=after, model=model)
    assert not nothing["written"] and nothing["why"] == "nothing recorded" and len(model.prompts) == 3


def test_the_review_is_written_on_schedule_and_once(vault: Vault) -> None:
    """A fake clock: catch-up on start, the draft at 23:00, the final at 04:00, nothing twice."""
    from capture.librarian import Librarian

    golden(vault)
    golden(vault, "2026-10-24")  # a day the computer was off at 04:00: no final
    model = Fake()
    librarian = Librarian(vault.root, ZONE, writer=model)
    # Catch-up only: the sample day (2026-10-20, a week back) and the 24th; the 26th has no records.
    assert librarian.step_review(moment("22:59:00")) == [("2026-10-20", "written"), ("2026-10-24", "written")]
    assert read_review(vault, "2026-10-24")["kind"] == "final" and read_review(vault, DAY) is None
    assert read_review(vault, "2026-10-20")["writer"]["name"] == "fake model"
    assert librarian.step_review(moment("23:00:00")) == [(DAY, "written")]
    assert read_review(vault, DAY)["kind"] == "draft"
    assert librarian.step_review(moment("23:10:00")) == []
    assert librarian.step_review(moment("01:30:00", "2026-10-28")) == []  # still the 27th: its draft stands
    assert librarian.step_review(moment("04:05:00", "2026-10-28"))[0][0] == DAY
    assert read_review(vault, DAY)["kind"] == "final"
    calls = len(model.prompts)
    assert librarian.step_review(moment("04:15:00", "2026-10-28")) == []
    restarted = Librarian(vault.root, ZONE, writer=model)  # the receiver started again: nothing to catch up
    assert restarted.step_review(moment("04:20:00", "2026-10-28")) == [] and len(model.prompts) == calls
    # A late change to the day (here a relabel) revises its final once.
    (vault.root / "label-corrections.json").write_text(json.dumps({"site:x.com": {"category": "reading"}}), "utf-8")
    assert restarted.step_review(moment("04:30:00", "2026-10-28")) == [(DAY, "revised: its facts changed")]
    assert restarted.step_review(moment("04:40:00", "2026-10-28")) == [] and len(model.prompts) == calls + 1
    daily = (vault.root / "notes" / "Daily" / f"{DAY}.md").read_text(encoding="utf-8")
    assert "## Summary" in daily and "A morning at the desk" in daily and "by fake model" in daily


# -- shown --------------------------------------------------------------------------------------------


def test_the_daily_note_opens_with_the_summary_and_the_conversations(vault: Vault) -> None:
    golden(vault)
    store = LifeStore(vault)
    write_review(vault, store, DAY, ZONE, now=moment("09:00:00", "2026-10-28"))
    write_review(vault, store, "2026-10-20", ZONE, now=moment("09:00:00", "2026-10-28"))  # the sample day
    notes = Notes(store)
    notes.write_day(DAY)
    notes.write_day("2026-10-20")
    daily = (vault.root / "notes" / "Daily" / f"{DAY}.md").read_text(encoding="utf-8")
    region = daily.split("<!-- daycube:start -->", 1)[1]
    assert region.lstrip().startswith("## Summary\n**2 h 30 min at the desk, mostly coding")
    assert region.index("## Summary") < region.index("## Apps")
    assert "- You were at the desk 2 h 30 min, from 09:00 to 12:00" in region
    assert "*Final review, written 2026-10-28 09:00 by template.*" in region
    sample = (vault.root / "notes" / "Daily" / "2026-10-20.md").read_text(encoding="utf-8")
    assert "## Conversations\n- 12:40 " in sample and "3 lines" in sample
    assert "Made from sample or simulated records." in sample  # the fixture day says it is a sample


def test_the_api_serves_writes_and_relabels_the_review(vault: Vault) -> None:
    golden(vault)
    auth = {"Authorization": f"Bearer {vault.token}"}
    with TestClient(create_app(Settings(vault_root=vault.root, vector_search=False, _env_file=None))) as client:
        view = client.get(f"/day/{DAY}/review").json()
        assert view["review"] is None and view["writers"]["summary"].startswith("template")
        assert [s["category"] for s in view["segments"] if s["where"] == "desk"] == ["coding", "social", "ai"]
        assert {"coding", "social", "other"} <= set(view["categories"]) and "private" not in view["categories"]
        assert view["window"] == {"t0": at("04:00:00"), "t1": at("04:00:00", "2026-10-28")}
        assert client.post(f"/day/{DAY}/review").status_code == 401
        made = client.post(f"/day/{DAY}/review", headers=auth).json()
        assert made["result"] == {"written": True, "why": "requested"} and made["review"]["kind"] == "final"
        assert "## Summary" in (vault.root / "notes" / "Daily" / f"{DAY}.md").read_text(encoding="utf-8")
        # Relabel the site of the social segment: the timeline and the review's numbers follow.
        social = next(s for s in made["segments"] if s["category"] == "social")
        assert (social["key"], social["broad"]) == ("page:https://x.com/home", "site:x.com")
        body = {"date": DAY, "key": social["broad"], "category": "reading"}
        assert client.post("/labels", json=body).status_code == 401
        changed = client.post("/labels", json=body, headers=auth).json()
        assert [c["name"] for c in changed["review"]["categories"]] == ["coding", "ai", "reading"]
        assert next(s for s in changed["segments"] if s["broad"] == "site:x.com")["labeled"] == "correction"
        cleared = client.post("/labels", json={"date": DAY, "key": "site:x.com", "clear": True}, headers=auth).json()
        assert [c["name"] for c in cleared["review"]["categories"]] == ["coding", "ai", "social"]
        project = client.post("/labels", json={"date": DAY, "key": "app:claude.exe", "project": "Launch"},
                              headers=auth).json()  # fmt: skip
        assert next(s for s in project["segments"] if s["category"] == "ai")["project"] == "Launch"
        assert [d["text"] for d in project["review"]["done"]] == [
            "DayCube: 1 h 30 min (coding 1 h 30 min)", "Launch: 40 min (ai 40 min)",
        ]  # fmt: skip
        for bad in ({"date": DAY, "key": "nonsense"}, {"date": DAY, "key": "site:x.com", "category": "Not OK!"}):
            assert client.post("/labels", json=bad, headers=auth).status_code == 400
        assert client.get("/day/2026-99-99/review").status_code == 400
        empty = client.get("/day/2026-11-05/review").json()
        assert empty["review"] is None and empty["segments"] == []


def test_a_simulated_day_is_marked_illustrative(tmp_path: Path) -> None:
    from ingest.review.simulate import simulate

    _, vault = simulate(tmp_path / "v", "2026-10-26", 1, noise=False, phone=True)
    out = write_review(vault, LifeStore(vault), "2026-10-26", ZONE, now=moment("09:00:00", "2026-10-28"))
    review = out["review"]
    assert review["illustrative"] is True
    assert {s["fidelity"] for s in build(vault, "2026-10-26", ZONE).segments} == {"illustrative"}
    contracts.validate("review", review)

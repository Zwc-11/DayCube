"""Phase 4 of the daily summary: planned blocks against the day, the hours long stretches happen,
the week in numbers, and yesterday's review pushed to the phone."""

import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from ingest import contracts
from ingest.config import Settings
from ingest.review import build, metrics, plan, push, summary, weekly
from ingest.review.summary import write_review
from ingest.store import LifeStore
from ingest.vault import Vault
from test_day_review import DAY, TZ, ZONE, at, golden

NOTE = """---
daycube: day 2026-10-27
---
<!-- daycube:start -->
## Plan
- 06:00-07:00 this is generated, not planned
<!-- daycube:end -->

# My day

## Plan
- [ ] 09:00–10:30 DayCube timeline
- 10:30-11 rust reading
- [x] 11:30 - 12:30: LeetCode
- 23:30-01:00 late night review
- no time here
- 25:00-26:00 not a time

## Notes
- 13:00-14:00 under another heading, not a block
"""


def facts_of(vault: Vault, day: str = DAY) -> metrics.Facts:
    return metrics.collect(vault, LifeStore(vault), build(vault, day, ZONE))


def write_note(vault: Vault, text: str = NOTE, day: str = DAY) -> Path:
    path = vault.root / "notes" / "Daily" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def clocks(blocks: list[plan.Block]) -> list[tuple[str, str, str]]:
    return [(datetime.fromtimestamp(b.t0, TZ).strftime("%d %H:%M"), datetime.fromtimestamp(b.t1, TZ).strftime("%d %H:%M"),
             b.text) for b in blocks]  # fmt: skip


# -- where the plan comes from ---------------------------------------------------------------------------


def test_the_daily_notes_plan_section_gives_the_blocks() -> None:
    assert clocks(plan.note_blocks(NOTE, DAY, TZ)) == [
        ("27 09:00", "27 10:30", "DayCube timeline"),
        ("27 10:30", "27 11:00", "rust reading"),
        ("27 11:30", "27 12:30", "LeetCode"),
        ("27 23:30", "28 01:00", "late night review"),
    ]


ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:CS 341 Lecture
DTSTART;TZID=America/Toronto:20260908T143000
DTEND;TZID=America/Toronto:20260908T155000
RRULE:FREQ=WEEKLY;BYDAY=TU,TH;UNTIL=20261205T045959Z
EXDATE;TZID=America/Toronto:20261020T143000
END:VEVENT
BEGIN:VEVENT
SUMMARY:Gym
DTSTART:20261027T220000Z
DTEND:20261027T230000Z
END:VEVENT
BEGIN:VEVENT
SUMMARY:Holiday
DTSTART;VALUE=DATE:20261027
DTEND;VALUE=DATE:20261028
END:VEVENT
BEGIN:VEVENT
SUMMARY:Cancelled meeting
STATUS:CANCELLED
DTSTART:20261027T150000Z
DTEND:20261027T160000Z
END:VEVENT
BEGIN:VEVENT
SUMMARY:Standup\\, daily
DTSTART;TZID=America/Toronto:20261020T091500
DTEND;TZID=America/Toronto:20261020T093000
RRULE:FREQ=DAILY;COUNT=5
END:VEVENT
END:VCALENDAR
"""


def test_calendar_files_give_the_days_events_with_repeats() -> None:
    start, stop = metrics.window(DAY, TZ)
    assert clocks(plan.ics_blocks(ICS, start, stop, TZ)) == [
        ("27 14:30", "27 15:50", "CS 341 Lecture"),  # Tuesdays and Thursdays
        ("27 18:00", "27 19:00", "Gym"),  # 22:00 UTC
    ]  # the daily standup ran 5 times from Oct 20: not on the 27th; all-day and cancelled events are not blocks
    start, stop = metrics.window("2026-10-20", TZ)
    assert clocks(plan.ics_blocks(ICS, start, stop, TZ)) == [("20 09:15", "20 09:30", "Standup, daily")]  # EXDATE
    start, stop = metrics.window("2026-12-08", TZ)
    assert plan.ics_blocks(ICS, start, stop, TZ) == []  # after UNTIL


# -- planned against actual --------------------------------------------------------------------------------


def test_planned_blocks_are_compared_with_the_day(vault: Vault) -> None:
    golden(vault)  # coding on DayCube 09:00-10:30, X 10:30-10:50, away, Claude 11:20-12:00, locked at 12:00
    write_note(vault)
    f = facts_of(vault)
    assert [(p["text"], p["status"], p["source"]) for p in f.plan] == [
        ("09:00–10:30 DayCube timeline: went as planned", "done", "note"),
        ("10:30–11:00 rust reading: went to social instead (20 min)", "elsewhere", "note"),
        ("11:30–12:30 LeetCode: went to ai instead (30 min)", "elsewhere", "note"),  # 30 of its 60 min, on Claude
        ("23:30–01:00 late night review: missed", "missed", "note"),
    ]  # fmt: skip
    assert f.numbers["plan"].display == "1 of 4"
    assert f.evidence["plan:2"]["text"] == "Planned 11:30–12:30: LeetCode"
    words = summary.template(f)
    said = next(s for s in words.sentences if s["text"].startswith("Of 4 planned"))
    assert said["text"] == "Of 4 planned blocks, 1 went as planned; 10:30–11:00 rust reading: went to social instead (20 min)."
    assert words.improvements[0]["metric"] == "plan"
    answer = {"headline": words.headline, "sentences": words.sentences, "improvements": words.improvements}
    assert summary.validate(answer, f, minimum=1) == []
    review = summary.assemble(f, words, {"name": "template", "attempts": 0, "rejected": []}, "final",
                              datetime.fromisoformat(at("23:00:00")))  # fmt: skip
    contracts.validate("review", review)
    assert review["plan"][0]["status"] == "done" and all(i in review["evidence"] for i in review["plan"][1]["evidence"])


# -- best hours ------------------------------------------------------------------------------------------


def timeline(vault: Vault, day: str, spans: list[tuple[str, str]]) -> None:
    folder = vault.day_path(day, must_exist=False)
    folder.mkdir(parents=True, exist_ok=True)
    rows = [{"t": at(a, day), "until": at(b, day), "where": "desk", "doing": "timeline.py", "category": "coding",
             "project": "DayCube", "talk": [], "switches": 0, "evidence": ["capture:app:x"], "source": "pc",
             "fidelity": "known", "labeled": "rule", "key": None, "broad": None} for a, b in spans]  # fmt: skip
    (folder / "timeline.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_tomorrow_keeps_the_hours_long_stretches_happen_in(vault: Vault) -> None:
    for back, spans in [(1, [("14:10:00", "15:40:00")]), (2, [("14:00:00", "15:10:00"), ("20:00:00", "20:20:00")]),
                        (3, [("09:00:00", "09:30:00"), ("14:30:00", "16:00:00")])]:  # fmt: skip
        timeline(vault, (datetime.fromisoformat(DAY) - timedelta(days=back)).date().isoformat(), spans)
    hours = plan.best_hours(vault, "2026-10-26", TZ)
    assert (hours["from"], hours["to"], hours["stretches"]) == ("14:00", "16:00", 4)
    golden(vault)
    f = facts_of(vault)
    item = next(t for t in f.tomorrow if t["kind"] == "hours")
    assert item["text"].startswith("Your stretches of 25 min or more in the last 14 days ran most between ")
    assert item["text"].endswith("keep that time for DayCube")
    assert all(i in f.background or i in f.evidence for i in item["evidence"])
    assert plan.best_hours(vault, "2026-10-20", TZ) is None  # no long stretches recorded before


# -- the week --------------------------------------------------------------------------------------------


def test_the_week_in_numbers_and_its_note(vault: Vault) -> None:
    golden(vault)  # Tuesday of the week 2026-10-26 to 2026-11-01
    store = LifeStore(vault)
    write_review(vault, store, DAY, ZONE, now=datetime.fromisoformat(at("05:00:00", "2026-10-28")))
    timeline(vault, "2026-10-29", [("10:00:00", "11:00:00")])
    timeline(vault, "2026-10-21", [("10:00:00", "10:20:00")])  # the week before
    now = datetime.fromisoformat(at("04:30:00", "2026-11-02"))
    assert weekly.due(now, ZONE) == "2026-11-01"
    out = weekly.write(vault, store, weekly.due(now, ZONE), ZONE, now=now)
    week = out["week"]
    assert out["written"] and week["week"] == "2026-W44" and (week["from"], week["to"]) == ("2026-10-26", "2026-11-01")
    tuesday = week["days"][1]
    assert (tuesday["desk_s"], tuesday["focus"], tuesday["category"], tuesday["project"], tuesday["review"]) == (
        9000.0, 2, "coding", "DayCube", "final"
    )  # fmt: skip
    assert week["totals"]["desk_s"] == 9000 + 3600 and week["totals"]["days_recorded"] == 2
    assert week["previous"] == {"desk_s": 1200.0, "focus": 0.0, "focus_s": 0.0}
    contracts.validate("week", week)
    note = (vault.root / "notes" / "Weekly" / "2026-W44.md").read_text(encoding="utf-8")
    assert "**3 h 30 min at the desk on 2 days; 3 stretches of 25 min or more, 3 h 10 min in all.**" in note
    assert "| 2026-10-27 | 2 h 30 min | 2 | 1 h 30 min | coding | DayCube | – | – | – |" in note
    assert weekly.write(vault, store, "2026-11-01", ZONE, now=now + timedelta(hours=1))["written"] is False


def test_the_librarian_writes_the_finished_week_once(vault: Vault) -> None:
    from capture.librarian import Librarian

    golden(vault)
    librarian = Librarian(vault.root, ZONE)
    write_review(vault, LifeStore(vault), DAY, ZONE, now=datetime.fromisoformat(at("05:00:00", "2026-10-28")))
    now = datetime.fromisoformat(at("04:40:00", "2026-11-02"))
    assert librarian.step_week(now) == "2026-W44"
    assert librarian.step_week(now + timedelta(minutes=10)) is None  # nothing changed
    assert (vault.root / "weeks" / "2026-W44.json").exists()


# -- the morning push ---------------------------------------------------------------------------------------


class Opener:
    def __init__(self, answer: bytes = b"ok", fail: bool = False) -> None:
        self.requests: list = []
        self.answer, self.fail = answer, fail

    def __call__(self, request) -> bytes:  # noqa: ANN001
        self.requests.append(request)
        if self.fail:
            raise OSError("network down")
        return self.answer


def test_yesterdays_review_is_pushed_once_after_the_push_time(vault: Vault) -> None:
    golden(vault)
    write_review(vault, LifeStore(vault), DAY, ZONE, now=datetime.fromisoformat(at("05:00:00", "2026-10-28")))
    settings = Settings(vault_root=vault.root, _env_file=None, summary_push="ntfy",
                        ntfy_url="https://ntfy.sh/daycube-k3j2h1")  # fmt: skip
    opener = Opener()
    assert push.step(vault, settings, datetime.fromisoformat(at("07:59:00", "2026-10-28")), ZONE, opener) is None
    sent = push.step(vault, settings, datetime.fromisoformat(at("08:05:00", "2026-10-28")), ZONE, opener)
    assert sent["date"] == DAY and sent["channel"] == "ntfy" and len(opener.requests) == 1
    request = opener.requests[0]
    assert request.full_url == "https://ntfy.sh/daycube-k3j2h1" and request.get_header("Title") == "DayCube · 2026-10-27".encode(
        "ascii", "replace").decode()  # fmt: skip
    text = request.data.decode()
    assert text.splitlines()[0] == "2 h 30 min at the desk, mostly coding; longest stretch 1 h 30 min"
    assert push.step(vault, settings, datetime.fromisoformat(at("08:15:00", "2026-10-28")), ZONE, opener) is None
    assert len(opener.requests) == 1  # once


def test_the_desktop_app_gets_yesterdays_review_for_its_morning_notification(vault: Vault, monkeypatch) -> None:  # noqa: ANN001
    import ingest.api

    golden(vault)
    write_review(vault, LifeStore(vault), DAY, ZONE, now=datetime.fromisoformat(at("05:00:00", "2026-10-28")))
    app = ingest.api.create_app(Settings(vault_root=vault.root, _env_file=None, vector_search=False))
    clock = {"now": datetime.fromisoformat(at("07:30:00", "2026-10-28"))}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return clock["now"].astimezone(tz)

    monkeypatch.setattr(ingest.api, "datetime", Clock)
    with TestClient(app) as client:
        assert client.get("/morning").json() == {"date": None}  # before 08:00
        clock["now"] = datetime.fromisoformat(at("08:01:00", "2026-10-28"))
        morning = client.get("/morning").json()
    assert morning["date"] == DAY and morning["title"] == "DayCube · 2026-10-27"
    assert morning["text"].startswith("2 h 30 min at the desk, mostly coding")


def test_a_failed_push_is_retried_a_few_times_and_telegram_must_accept(vault: Vault) -> None:
    golden(vault)
    write_review(vault, LifeStore(vault), DAY, ZONE, now=datetime.fromisoformat(at("05:00:00", "2026-10-28")))
    settings = Settings(vault_root=vault.root, _env_file=None, summary_push="telegram", telegram_token="123:abc",
                        telegram_chat="42")  # fmt: skip
    down = Opener(fail=True)
    times = [datetime.fromisoformat(at("08:00:00", "2026-10-28")) + timedelta(minutes=10 * k) for k in range(7)]
    results = [push.step(vault, settings, t, ZONE, down) for t in times]
    assert [r["attempts"] for r in results if r] == [1, 2, 3, 4, 5] and results[-1] is None
    assert "network down" in results[0]["error"]
    assert json.loads(down.requests[0].data)["chat_id"] == "42"
    assert down.requests[0].full_url == "https://api.telegram.org/bot123:abc/sendMessage"
    refused = push.send(settings, "t", "x", Opener(answer=b'{"ok": true}'))
    assert refused == "telegram"
    off = Settings(vault_root=vault.root, _env_file=None)
    assert push.step(vault, off, times[0], ZONE, down) is None


# -- finished on websites --------------------------------------------------------------------------------


def test_accepted_leetcode_submissions_count_as_done(vault: Vault) -> None:
    from ingest.review import sites
    from test_day_review import log

    golden(vault)

    def page(clock: str, url: str, text: str, title: str = "LeetCode") -> dict:
        return {"t": at(clock), "kind": "blocks", "doc": f"d{clock}", "url": url, "title": title,
                "blocks": [{"id": "b1", "tag": "div", "text": text}]}  # fmt: skip

    log(vault, "blocks.jsonl", [
        page("10:31:00", "https://leetcode.com/problems/two-sum/submissions/1530001/", "Accepted 63 / 63 testcases passed"),
        page("10:35:00", "https://leetcode.com/problems/two-sum/submissions/1530001/", "Accepted again"),  # the same page
        page("10:40:00", "https://leetcode.com/problems/lru-cache/submissions/1530002/", "Wrong Answer"),
        page("10:45:00", "https://leetcode.com/problems/lru-cache/description/", "Accepted 12M"),  # not a submission
        page("10:48:00", "https://learn.uwaterloo.ca/d2l/le/dropbox/1/submit", "File submission successful", "A3"),
    ])  # fmt: skip
    f = facts_of(vault)
    assert f.numbers["sites"].value == 1
    assert [d["text"] for d in f.done if d["kind"] == "site"] == ["LeetCode: 1 accepted (two sum)"]
    words = summary.template(f)
    assert any(s["text"] == "Finished on websites: LeetCode 1 accepted." for s in words.sentences)
    answer = {"headline": words.headline, "sentences": words.sentences, "improvements": words.improvements}
    assert summary.validate(answer, f, minimum=1) == []
    (vault.root / "sites.yaml").write_text(
        "sites:\n  - name: LEARN\n    domain: learn.uwaterloo.ca\n    path: /dropbox/\n    text: submission successful\n"
        "    label: submitted\n  - name: broken\n", "utf-8")  # fmt: skip
    found, errors = sites.rules(vault)
    assert [r.name for r in found] == ["LEARN", "LeetCode"] and errors == ["sites.yaml site 2 left out: KeyError"]
    f = facts_of(vault)
    assert sorted(d["text"] for d in f.done if d["kind"] == "site") == ["LEARN: 1 submitted (A3)", "LeetCode: 1 accepted (two sum)"]
    assert "sites.yaml site 2 left out: KeyError" in f.gaps

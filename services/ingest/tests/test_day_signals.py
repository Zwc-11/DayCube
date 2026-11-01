"""Phase 3 of the daily summary: typing and mouse counts, the lines you note, phone apps and
commits, from the receiver that records them to the numbers and words of the day's review."""

import json
import os
import struct
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ingest.config import Settings
from ingest.review import build, metrics, signals, summary
from ingest.review import git as gitlog
from ingest.store import LifeStore
from ingest.vault import Vault
from test_day_review import DAY, TZ, ZONE, at, golden, log


def facts_of(vault: Vault, day: str = DAY) -> metrics.Facts:
    return metrics.collect(vault, LifeStore(vault), build(vault, day, ZONE))


def passes(f: metrics.Facts) -> list[str]:
    """The template's words held to the validator, as a model's would be."""
    words = summary.template(f)
    answer = {"headline": words.headline, "sentences": words.sentences, "improvements": words.improvements}
    return summary.validate(answer, f, minimum=1)


def bucket(clock: str, keys: int = 0, clicks: int = 0, wheel: int = 0, move: int = 0) -> dict:
    return {"t": at(clock), "s": 10, "keys": keys, "clicks": clicks, "wheel": wheel, "move_px": move}


# -- typing and mouse counts ---------------------------------------------------------------------------


def test_input_counts_tell_desk_time_with_input_from_without(vault: Vault) -> None:
    golden(vault)  # coding 09:00-10:30, X 10:30-10:50, away, Claude 11:20-12:00
    records = [bucket(f"09:{m:02d}:{s:02d}", keys=30, clicks=2, move=400) for m in range(0, 30) for s in (0, 10, 20)]
    records += [bucket("11:30:00", clicks=5, wheel=3), bucket("13:00:00", keys=99)]  # 13:00: away from the desk
    records += [{"t": at("09:31:00"), "s": 10}, "torn", {"no": "time"}]  # empty, torn and unreadable lines
    folder = vault.root / "capture" / DAY
    with (folder / "inputs.jsonl").open("a", encoding="utf-8") as stream:
        for r in records:
            stream.write((json.dumps(r) if isinstance(r, dict) else r) + "\n")
    f = facts_of(vault)
    n = f.numbers
    assert n["active"].value == 30 * 3 * 10 + 10 and n["active"].display == "15 min (10%)"
    assert n["keys"].value == 30 * 3 * 30 + 99 and n["keys"].display == "2,799"
    assert n["clicks"].value == 30 * 3 * 2 + 5
    assert all(i.startswith("timeline:") for i in n["active"].evidence)
    words = summary.template(f)
    assert any("Typing or the mouse were in use for 15 min (10%)" in s["text"] and "2,799 keys" in s["text"]
               for s in words.sentences)  # fmt: skip
    assert passes(f) == []
    assert not any("typing and mouse" in g for g in f.gaps)


def test_input_counts_keep_no_key_identity() -> None:
    from capture.inputs import RIM_TYPEKEYBOARD, RIM_TYPEMOUSE, Buckets, Counts, parse

    header = 24  # x64 RAWINPUTHEADER

    def keyboard(vkey: int, released: bool, device: int = 0x10045) -> bytes:
        return struct.pack("<IIQQ", RIM_TYPEKEYBOARD, header + 16, device, 0) + struct.pack(
            "<HHHHII", 30, 1 if released else 0, 0, vkey, 0x100, 0
        )

    def mouse(flags: int, buttons: int, data: int, dx: int, dy: int, device: int = 0x10047) -> bytes:
        return struct.pack("<IIQQ", RIM_TYPEMOUSE, header + 24, device, 0) + struct.pack(
            "<H2xHHIiiI", flags, buttons, data, 0, dx, dy, 0
        )

    assert parse(keyboard(0x41, False), header) == (RIM_TYPEKEYBOARD, (0x41, False), 0x10045)
    assert parse(mouse(0, 0x0001 | 0x0400, 0x10000 - 240, 3, 4), header) == (
        RIM_TYPEMOUSE, (0, 0x0401, 65296, 3, 4), 0x10047
    )  # fmt: skip
    assert parse(keyboard(0x7E, False, device=0), header)[2] == 0  # F15 from a keep-awake tool: no device
    assert parse(b"\x02\x00\x00\x00" + bytes(40), header) is None  # an HID device
    counts = Counts()
    for code in (0x41, 0x41, 0x41):  # a held key's auto-repeat is one press
        counts.key(code, released=False)
    counts.key(0x41, released=True)
    counts.key(0x41, released=False)
    counts.mouse(0, 0x0001 | 0x0004, 0, 3, 4)  # two buttons pressed, moved 5 px
    counts.mouse(0, 0x0400, 0x10000 - 240, 0, 0)  # wheel two notches back
    counts.mouse(1, 0, 0, 0, 0)  # absolute (a tablet): its first position is not a move
    counts.mouse(1, 0, 0, 65536 // 2, 0)  # half the desktop's width on a 2560 px screen
    assert counts.take() == {"keys": 2, "clicks": 2, "wheel": 2, "move_px": 1285}
    clock = [1000.0]
    written: list[dict] = []
    private = [False]
    buckets = Buckets(written.append, lambda: private[0], clock=lambda: clock[0])
    buckets.event(RIM_TYPEKEYBOARD, (0x51, False))
    private[0] = True
    buckets.event(RIM_TYPEKEYBOARD, (0x52, False))  # a password manager in front: not counted
    private[0] = False
    clock[0] = 1012.0
    buckets.tick()
    clock[0] = 1035.0
    buckets.tick()  # a bucket with nothing in it writes nothing
    assert len(written) == 1 and buckets.skipped_private == 1
    assert set(written[0]) == {"t", "s", "keys", "clicks", "wheel", "move_px", "injected"}  # counts, no identities
    assert written[0]["keys"] == 1 and written[0]["s"] == 10 and written[0]["injected"] == 0


def test_software_input_is_not_a_person_at_the_desk() -> None:
    """Keys and moves a program sends (no device) are kept apart and do not reset the idle time
    the screen reader's away uses: the PC that agents kept busy all night was counted as attended."""
    from capture.inputs import RIM_TYPEKEYBOARD, RIM_TYPEMOUSE, Buckets

    clock = [5000.0]
    written: list[dict] = []
    buckets = Buckets(written.append, clock=lambda: clock[0])
    buckets.event(RIM_TYPEKEYBOARD, (0x41, False), device=0x10045)  # you press a key
    for minute in range(1, 7):  # then a program presses F15 every minute
        clock[0] = 5000.0 + 60 * minute
        buckets.event(RIM_TYPEKEYBOARD, (0x7E, False), device=0)
        buckets.event(RIM_TYPEMOUSE, (0, 0, 0, 1, 0), device=0)
    assert buckets.idle_s() == 360.0
    buckets.tick()
    assert [r["keys"] for r in written] == [1, 0, 0, 0, 0, 0] and [r["injected"] for r in written][1:] == [2] * 5
    assert buckets.injected_total == 12


def test_the_screen_reader_goes_away_on_real_input_alone(vault: Vault) -> None:
    from capture.api import create_capture_app
    from test_screen import changes, reader_for

    app = create_capture_app(Settings(vault_root=vault.root, _env_file=None), receive=True)
    with TestClient(app):
        receiver = app.state.receiver
        reader, system, ocr, clock = reader_for(vault, receiver)
        judged: list = [(0.0, "devices")]
        receiver.human_idle = lambda system_idle: judged[0]
        reader.step()
        system.idle, judged[0] = 2.0, (400.0, "software")  # Windows saw input a moment ago; a program sent it
        reader.step()
        judged[0] = (0.0, "devices")
        reader.step()
        receiver.human_idle = lambda system_idle: None  # no counter: Windows' idle time, as before
        system.idle = 400.0
        reader.step()
    kinds = [(r["kind"], r.get("why"), r.get("input")) for r in changes(vault)]
    assert kinds == [("window", None, None), ("away", "idle", "software"), ("window", None, None),
                     ("away", "idle", None)]  # fmt: skip


def test_input_the_counter_cannot_see_is_not_absence() -> None:
    """Windows' idle time decides when it saw input later than any the counter got, from a device or
    from a program (a window run as administrator in front; touch or pen)."""
    from capture.inputs import RIM_TYPEKEYBOARD, Buckets

    clock = [1000.0]
    buckets = Buckets(lambda record: None, clock=lambda: clock[0])
    buckets.event(RIM_TYPEKEYBOARD, (0x41, False), device=0x10045)
    clock[0] = 1600.0
    assert buckets.idle_given(600.0) == (600.0, "devices")  # Windows agrees: nobody since
    assert buckets.idle_given(5.0) == (5.0, "unseen")  # Windows saw input the counter did not
    buckets.event(RIM_TYPEKEYBOARD, (0x7E, False), device=0)  # a program presses F15
    clock[0] = 1601.0
    assert buckets.idle_given(1.0) == (601.0, "software")
    clock[0] = 1700.0
    assert buckets.idle_given(30.0) == (30.0, "unseen")  # later input, and no program sent anything since


def test_the_receiver_files_counts_and_notes_and_drops_them_while_paused(vault: Vault) -> None:
    from capture.api import create_capture_app

    settings = Settings(vault_root=vault.root, _env_file=None)
    auth = {"Authorization": f"Bearer {vault.token}"}
    with TestClient(create_capture_app(settings, receive=True)) as client:
        receiver = client.app.state.receiver
        receiver.input_counts(bucket("09:00:00", keys=4))
        assert client.post("/doing", json={"text": "  reading   CS 341 notes "}).status_code == 401
        noted = client.post("/doing", json={"text": "  reading   CS 341 notes "}, headers=auth).json()
        assert noted["text"] == "reading CS 341 notes" and len(noted["id"]) == 12
        assert client.post("/doing", json={"text": " "}, headers=auth).status_code == 422
        assert client.get("/status").json()["inputs"]["error"] == "not started"
        client.post("/pause", headers=auth)
        receiver.input_counts(bucket("09:00:10", keys=9))
        assert client.post("/doing", json={"text": "x"}, headers=auth).status_code == 409
    days = [p for p in (vault.root / "capture").glob("*/inputs.jsonl")]
    assert [json.loads(line)["keys"] for line in days[0].open()] == [4]
    notes = [json.loads(line) for p in (vault.root / "capture").glob("*/doing.jsonl") for line in p.open()]
    assert [n["text"] for n in notes] == ["reading CS 341 notes"]


# -- the lines you note ----------------------------------------------------------------------------------


def test_noted_lines_are_cited_with_their_time(vault: Vault) -> None:
    golden(vault)
    log(vault, "doing.jsonl", [
        {"t": at("09:05:00"), "id": "n1", "text": "Fixing the splat budget for CS 341 demo"},
        {"t": at("11:25:00"), "id": "n2", "text": "asking Claude about ownership"},
        {"t": at("03:30:00"), "id": "n0", "text": "before the review day began"},
    ])  # fmt: skip
    f = facts_of(vault)
    assert f.numbers["noted"].value == 2
    assert f.evidence["capture:doing:n1"]["text"] == "Noted: Fixing the splat budget for CS 341 demo"
    assert "capture:doing:n0" not in f.evidence
    words = summary.template(f)
    said = next(s for s in words.sentences if s["text"].startswith("You noted"))
    assert said["text"] == ("You noted “Fixing the splat budget for CS 341 demo” at 09:05 and "
                            "“asking Claude about ownership” at 11:25.")  # fmt: skip
    assert said["evidence"] == ["capture:doing:n1", "capture:doing:n2"]
    assert passes(f) == []
    assert f.top["doing"] == ["capture:doing:n1", "capture:doing:n2"]  # shown to a model too


# -- phone apps --------------------------------------------------------------------------------------------


def phone_events(vault: Vault, events: list[tuple[str, str, str]]) -> None:
    log(vault, "phone-events.jsonl", [
        {"t": at(clock), "id": f"e{n}", "app": app, "event": kind, "device": "ph-1"}
        for n, (clock, app, kind) in enumerate(events)
    ])  # fmt: skip


def test_phone_sessions_pickups_and_desk_time(vault: Vault) -> None:
    golden(vault)  # at the desk 09:00-10:50 and 11:20-12:00
    phone_events(vault, [
        ("09:10:00", "Instagram", "opened"), ("09:10:30", "Instagram", "opened"),  # a repeated opening
        ("09:25:00", "Instagram", "closed"),
        ("09:25:20", "WeChat", "opened"), ("09:30:00", "WeChat", "closed"),  # within a minute: the same pickup
        ("10:55:00", "TikTok", "opened"),  # its closing was missed: ends at the next opening
        ("11:05:00", "Instagram", "opened"), ("11:35:00", "Instagram", "closed"),
        ("12:30:00", "Maps", "opened"),  # never closed: counts at most 30 min
    ])  # fmt: skip
    sessions = signals.phone(vault, [DAY], *metrics.window(DAY, TZ))
    assert [(s.app, s.t1 - s.t0, s.closed) for s in sessions] == [
        ("Instagram", 900, True), ("WeChat", 280, True), ("TikTok", 600, False), ("Instagram", 1800, True),
        ("Maps", 1800, False),
    ]  # fmt: skip
    f = facts_of(vault)
    n = f.numbers
    assert n["phone"].display == "1 h 30 min" and n["pickups"].value == 3  # TikTok to Instagram: one pickup
    assert n["phone_desk"].value == 900 + 280 + 900  # Instagram 09:10-09:25, WeChat, Instagram 11:20-11:35
    assert [(a.name, a.seconds) for a in f.apps] == [("Instagram", 2700), ("Maps", 1800), ("TikTok", 600), ("WeChat", 280)]
    assert f.evidence["phone:e8"]["fidelity"] == "estimated"  # Maps: no closing seen
    words = summary.template(f)
    said = next(s for s in words.sentences if s["text"].startswith("On the phone"))
    assert said["text"] == ("On the phone: Instagram 45 min, Maps 30 min and TikTok 10 min, in 3 pickups; "
                            "35 min of it during desk time.")  # fmt: skip
    assert [i["metric"] for i in words.improvements][:1] == ["phone_desk"]  # 35 min of desk time: the first change
    assert passes(f) == []
    review = summary.assemble(f, words, {"name": "template", "attempts": 0, "rejected": []}, "final",
                              datetime.fromisoformat(at("23:00:00")))  # fmt: skip
    from ingest import contracts

    contracts.validate("review", review)
    assert review["apps"][0]["display"] == "45 min (50%)"


def test_the_phone_posts_app_events_once_each(vault: Vault) -> None:
    from capture.api import create_capture_app

    settings = Settings(vault_root=vault.root, vector_search=False, _env_file=None)
    with TestClient(create_capture_app(settings, receive=True)) as client:
        assert client.post("/phone/events", json={"app": "Instagram", "event": "opened"}).status_code == 401
        paired = client.post("/phone/pair", json={"name": "iPhone"}, headers={"Authorization": f"Bearer {vault.token}"})
        auth = {"Authorization": f"Bearer {paired.json()['token']}"}
        event = {"app": "Instagram", "event": "opened", "t": at("09:10:00")}
        assert client.post("/phone/events", json=event, headers=auth).json() == {"accepted": 1, "duplicates": 0,
                                                                                   "paused": False}  # fmt: skip
        many = [event, {"app": "Instagram", "event": "closed", "t": at("09:25:00")}]
        assert client.post("/phone/events", json=many, headers=auth).json()["duplicates"] == 1  # a retry
        assert client.post("/phone/events", json={"app": "X", "event": "swiped"}, headers=auth).status_code == 422
        (vault.root / "capture" / "paused").write_text("", encoding="utf-8")
        assert client.post("/phone/events", json=event | {"t": at("10:00:00")}, headers=auth).json()["paused"] is True
    lines = (vault.root / "capture" / DAY / "phone-events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [(json.loads(x)["event"], json.loads(x)["t"][11:16]) for x in lines] == [("opened", "09:10"), ("closed", "09:25")]


# -- commits -------------------------------------------------------------------------------------------------


def git(folder: Path, *args: str, when: str | None = None, email: str = "caesar@example.com", name: str = "A") -> None:
    keep = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k.startswith("GIT_CONFIG")}
    env = keep | {"GIT_AUTHOR_DATE": when or "", "GIT_COMMITTER_DATE": when or "", "GIT_AUTHOR_EMAIL": email,
                  "GIT_COMMITTER_EMAIL": email, "GIT_AUTHOR_NAME": name, "GIT_COMMITTER_NAME": name,
                  "HOME": str(folder), "USERPROFILE": str(folder), "GIT_CONFIG_NOSYSTEM": "1"}  # fmt: skip
    subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True,
                   env={k: v for k, v in env.items() if v})  # fmt: skip


@pytest.fixture
def no_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The machine's own git config (a global user.email) stays out of the reader."""
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.mark.usefixtures("no_git_config")
def test_commits_in_the_day_are_counted_as_done_yours_apart_from_others(vault: Vault, tmp_path: Path) -> None:
    golden(vault)
    repo = tmp_path / "DAYCUBE"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "caesar@example.com")
    for clock, subject, email, name in [("08:00:00", "before the day? no: after 04:00", "caesar@example.com", "A"),
                                        ("09:40:00", "feat(review): phone apps", "caesar@example.com", "A"),
                                        ("10:00:00", "fix(viewer): an agent's fix", "noreply@anthropic.com", "Claude"),
                                        ("10:30:00", "another", "noreply@anthropic.com", "Claude"),
                                        ("03:00:00", "the night before 04:00", "caesar@example.com", "A")]:  # fmt: skip
        (repo / f"{clock[:2]}{clock[3:5]}.txt").write_text(subject, encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", subject, when=at(clock), email=email, name=name)
    (vault.root / "projects.yaml").write_text(f"projects:\n  DayCube:\n    folders: ['{repo}']\n", "utf-8")
    assert gitlog.repositories(vault) == [("DayCube", repo)]
    f = facts_of(vault)
    assert f.numbers["commits"].value == 2
    assert f.numbers["commits_others"].value == 2
    assert f.numbers["commits_others"].parts == {"authors": {"Claude": 2}}
    done = [d for d in f.done if d["kind"] == "commits"]
    assert done == [{"text": "DayCube: 2 commits by you (before the day? no: after 04:00; feat(review): phone apps); "
                             "2 by Claude", "kind": "commits", "evidence": done[0]["evidence"]}]  # fmt: skip
    assert all(f.evidence[i]["kind"] == "commit" for i in done[0]["evidence"])
    assert any(f.evidence[i]["text"] == "Commit in DayCube (by Claude): another" for i in done[0]["evidence"])
    words = summary.template(f)
    assert any(s["text"] == "You made 2 commits in DayCube; Claude made 2." for s in words.sentences)
    assert passes(f) == []


@pytest.mark.usefixtures("no_git_config")
def test_without_your_email_every_commit_is_yours(vault: Vault, tmp_path: Path) -> None:
    golden(vault)
    repo = tmp_path / "DAYCUBE"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("a", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "one", when=at("09:00:00"), email="noreply@anthropic.com", name="Claude")
    (vault.root / "projects.yaml").write_text(f"projects:\n  DayCube:\n    folders: ['{repo}']\n", "utf-8")
    f = facts_of(vault)
    assert f.numbers["commits"].value == 1
    assert "commits_others" not in f.numbers


def test_without_signals_the_gaps_say_what_is_not_recorded(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    golden(vault)
    f = facts_of(vault)
    assert "Not recorded for this day: typing and mouse use and phone apps (iOS Shortcuts, docs/phone.md)." in f.gaps
    assert {"active", "keys", "noted", "phone", "commits"}.isdisjoint(f.numbers)
    monkeypatch.setenv("DAYCUBE_GIT", "off")
    assert metrics._reads_git(vault) is False


# -- the doing-now box -------------------------------------------------------------------------------------


def test_the_hotkey_opens_one_box_at_a_time() -> None:
    from capture.doingnow import DoingBox

    release = threading.Event()
    saved: list[str] = []

    def ask() -> str | None:
        release.wait(2)
        return "  writing the plan "

    box = DoingBox(saved.append, ask=ask)
    first = box.on_hotkey()
    assert first is not None and box.on_hotkey() is None  # pressed again while the box is open
    release.set()
    first.join(2)
    assert saved == ["  writing the plan "] and box.opened == 1 and box.saved == 1
    second = box.on_hotkey()
    assert second is not None
    second.join(2)
    assert box.opened == 2


def test_the_doctor_reports_the_counter_and_the_box(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    from zoneinfo import ZoneInfo

    from capture import doctor

    now = datetime.now(TZ)
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    base = {"paused": False, "browser": {"last": now.isoformat()}, "reading": None, "gaze": {"tracking": False}}

    def found(**status: object) -> dict[str, tuple[str, str]]:
        return {name: (v, d) for v, name, d in doctor.checks(base | status, vault.root, ZoneInfo(ZONE), now, None)}

    checks = found(inputs={"running": True, "last": "2026-10-31T16:40:10-04:00", "skipped_private": 3,
                           "injected": 412, "injected_last": "2026-10-31T16:41:00-04:00"},
                   doing={"running": True, "saved": 2})  # fmt: skip
    assert checks["software input"][0] == doctor.INFO and "412 key and mouse events" in checks["software input"][1]
    assert checks["typing and mouse"] == (
        doctor.PASS, "counted in 10 s buckets (counts only): last count at 16:40; 3 events in private windows left out"
    )  # fmt: skip
    assert checks["doing-now box"] == (doctor.PASS, "Alt+Shift+N opens it; 2 lines since start")
    checks = found(inputs={"running": False, "error": "OSError: [WinError 5]"}, doing={"running": False,
                   "error": "OSError: [WinError 1409] Hot key is already registered"})  # fmt: skip
    assert checks["typing and mouse"][0] == doctor.WARN and "WinError 5" in checks["typing and mouse"][1]
    assert "1409" in checks["doing-now box"][1]
    assert "typing and mouse" not in found()  # an older receiver
    for name in ("inputs", "doing", "phone-events"):
        assert name in checks["today's logs"][1]


def test_the_box_runs_in_its_own_process_and_its_answer_comes_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from capture import doingnow

    calls = []

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='noise\n{"text": "reviewing PR #12"}\n', stderr="")

    monkeypatch.setattr(doingnow.subprocess, "run", run)
    assert doingnow.ask_apart() == "reviewing PR #12"
    assert calls[0][1:] == ["-m", "capture.doingnow", "ask"]
    monkeypatch.setattr(doingnow.subprocess, "run", lambda c, **o: subprocess.CompletedProcess(c, 0, '{"text": null}\n', ""))
    assert doingnow.ask_apart() is None  # Escape, or no answer in time

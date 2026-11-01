"""The day timeline (ingest/review): what was in front, away, places and talk joined into one
sequence of segments per review day, each with its category and project."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from ingest import contracts
from ingest.review import build, review_day, window, write
from ingest.review.labels import CACHE_FILE, Activity, Labels, editor_folder, repository
from ingest.review.simulate import ZONE, score, simulate
from ingest.vault import Vault, read_lines

TZ = ZoneInfo(ZONE)
DAY = "2026-10-27"
CODE = "timeline.py - DAYCUBE - Visual Studio Code"


def at(clock: str, day: str = DAY) -> str:
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=TZ).isoformat()


def log(vault: Vault, name: str, records: list[dict]) -> None:
    """Append records to the capture log of each record's local day, as the receiver files them."""
    for n, record in enumerate(records):
        record.setdefault("id", f"{name[0]}{len(records)}-{n}-{record['t'][11:19].replace(':', '')}")
        day = datetime.fromisoformat(record["t"]).astimezone(TZ).date().isoformat()
        folder = vault.root / "capture" / day
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / name).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")


def app(t: str, program: str, title: str) -> dict:
    return {"t": t, "kind": "window", "app": program, "title": title, "url": f"app://{program}/x"}


def away(t: str, why: str, since: str | None = None) -> dict:
    return {"t": t, "kind": "away", "why": why} | ({"since": since} if since else {})


def spans(segments: list[dict]) -> list[tuple]:
    return [(s["t"][11:19], s["until"][11:19], s["where"], s["category"]) for s in segments]


# -- simulated days: the plan's targets ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "seed", "phone"), [("2026-10-26", 1, True), ("2026-10-31", 6, False), ("2026-10-28", 3, True)]
)
def test_simulated_days_are_right_second_by_second(tmp_path: Path, day: str, seed: int, phone: bool) -> None:
    """Clean records give every second its true label; with the noise of real capture, at least
    98% of seconds and boundaries within 2 s (median). 2026-10-31 has 25 hours."""
    scripted, vault = simulate(tmp_path / "clean", day, seed, noise=False, phone=phone)
    clean = score(scripted, build(vault, day, ZONE).segments)
    assert clean["accuracy"] == 1.0, clean["wrong"]
    assert clean["switches_found"] == clean["switches_true"] > 0
    assert clean["talk"][0] == clean["talk"][1]
    scripted, vault = simulate(tmp_path / "noisy", day, seed, noise=True, phone=phone)
    noisy = score(scripted, build(vault, day, ZONE).segments)
    assert noisy["accuracy"] >= 0.98, noisy["wrong"]
    assert noisy["median_boundary_s"] <= 2.0
    assert noisy["talk"][0] == noisy["talk"][1]


def test_the_review_day_runs_from_four_to_four() -> None:
    start, stop = window("2026-10-31", TZ)  # the clocks go back on 1 November
    assert (start.isoformat(), stop.isoformat()) == ("2026-10-31T04:00:00-04:00", "2026-11-01T04:00:00-05:00")
    assert stop.timestamp() - start.timestamp() == 25 * 3600
    assert review_day(datetime.fromisoformat(at("03:59:59", "2026-10-28")), TZ) == "2026-10-27"
    assert review_day(datetime.fromisoformat(at("04:00:00", "2026-10-28")), TZ) == "2026-10-28"


# -- from the records -------------------------------------------------------------------------------


def test_a_window_in_front_across_midnight_is_one_segment(vault: Vault) -> None:
    """Capture folders are calendar days; the record that ends a window can be in the next one."""
    log(vault, "windows.jsonl", [app(at("23:30:00"), "code.exe", CODE), away(at("00:40:00", "2026-10-28"), "locked")])
    assert spans(build(vault, DAY, ZONE).segments) == [("23:30:00", "00:40:00", "desk", "coding")]
    assert build(vault, DAY, ZONE).segments[0]["project"] == "DAYCUBE"  # the folder the editor has open
    assert build(vault, "2026-10-28", ZONE).segments == []  # before 04:00 it was still the 27th


def test_away_is_the_screen_readers_and_paused_time_is_not_recorded(vault: Vault) -> None:
    log(vault, "windows.jsonl", [
        app(at("09:00:00"), "code.exe", CODE),
        away(at("09:30:00"), "idle", since=at("09:25:00")),  # away from the last input
        app(at("09:40:00"), "code.exe", CODE),
        away(at("09:41:00"), "paused"),
        app(at("10:00:00"), "code.exe", CODE),
        away(at("10:10:00"), "locked"),
        app(at("10:30:00"), "code.exe", CODE),
        # Input that comes back every five minutes on its own: away, back a second, away again.
        away(at("11:00:00"), "idle", since=at("10:55:00")),
        app(at("11:00:01"), "code.exe", CODE),
        away(at("11:05:01"), "idle", since=at("11:00:01")),
        app(at("11:05:02"), "code.exe", CODE),
        away(at("11:30:00"), "locked"),
    ])  # fmt: skip
    segments = build(vault, DAY, ZONE).segments
    assert spans(segments) == [
        ("09:00:00", "09:25:00", "desk", "coding"),
        ("09:25:00", "09:40:00", "away", None),
        ("09:40:00", "09:41:00", "desk", "coding"),  # 09:41 to 10:00 was paused: no segment
        ("10:00:00", "10:10:00", "desk", "coding"),
        ("10:10:00", "10:30:00", "away", None),
        ("10:30:00", "10:55:00", "desk", "coding"),
        ("10:55:00", "11:05:02", "away", None),
        ("11:05:02", "11:30:00", "desk", "coding"),
    ]
    assert segments[1]["fidelity"] == "estimated" and segments[4]["fidelity"] == "known"  # idle is inferred
    assert all(e.startswith("capture:away:") for e in segments[6]["evidence"])
    contracts.validate_all("timeline", segments)


def test_hours_in_one_window_count_in_full_while_the_reader_says_it_records(vault: Vault) -> None:
    """Three hours in one window and one browser tab, the reader writing "still" every 5 minutes:
    nothing is cut at the two hours allowed a window from a reader that never says so."""
    video = {"tab": 2, "window": 1, "url": "https://www.youtube.com/watch?v=long", "title": "A long talk - YouTube"}
    still = [{"t": (datetime.fromisoformat(at("09:00:30")) + timedelta(minutes=5 * k)).isoformat(), "kind": "still"}
             for k in range(72)]  # fmt: skip
    log(vault, "windows.jsonl", [
        app(at("09:00:00"), "code.exe", CODE), {"t": at("12:00:00"), "kind": "window", "app": "opera.exe", "browser": True},
        *still, {"t": at("15:00:00"), "kind": "away", "why": "locked"},
    ])  # fmt: skip
    log(vault, "browser.jsonl", [{"t": at("12:00:00.100"), "kind": "focus", **video}])
    assert spans(build(vault, DAY, ZONE).segments) == [
        ("09:00:00", "12:00:00", "desk", "coding"),
        ("12:00:00", "15:00:00", "desk", "watching"),
    ]


def test_a_reader_that_falls_silent_ends_what_was_in_front(vault: Vault) -> None:
    """A reader writing "still" every five minutes that stops (the computer switched off): what
    was in front ends at its last record, not two hours later. The units keep one span per app."""
    from ingest.capture import capture_units

    log(vault, "windows.jsonl", [
        app(at("09:00:00"), "code.exe", CODE),
        {"t": at("09:00:30"), "kind": "still"}, {"t": at("09:05:30"), "kind": "still"},
        {"t": at("09:10:30"), "kind": "still"},
        app(at("11:00:00"), "claude.exe", "Claude"),  # switched on again
        *({"t": at(f"11:{m:02d}:30"), "kind": "still"} for m in (0, 5, 10, 15)),
        away(at("11:20:00"), "locked"),
    ])  # fmt: skip
    assert spans(build(vault, DAY, ZONE).segments) == [
        ("09:00:00", "09:10:30", "desk", "coding"),
        ("11:00:00", "11:20:00", "desk", "ai"),
    ]
    units = [u for u in capture_units(vault, DAY) if u["type"] == "screen.app"]
    assert [u["text"].split(":")[0] for u in units] == ["Used Visual Studio Code for 120 min", "Used Claude for 20 min"]


def test_glances_are_switches_and_pages_come_from_the_extension(vault: Vault) -> None:
    learn = "https://learn.uwaterloo.ca/d2l/le/content/123/viewContent/9"
    log(vault, "windows.jsonl", [
        app(at("09:00:00"), "code.exe", CODE),
        {"t": at("09:10:00"), "kind": "window", "app": "opera.exe", "browser": True},
        app(at("09:10:08"), "code.exe", CODE),  # 8 s in the browser: a glance
        {"t": at("09:20:00"), "kind": "window", "app": "opera.exe", "browser": True},
        away(at("09:50:00"), "locked"),
    ])  # fmt: skip
    log(vault, "browser.jsonl", [
        {"t": at("09:10:00.200"), "kind": "focus", "tab": 4, "window": 1, "url": "https://x.com/home", "title": "Home / X"},
        {"t": at("09:10:08.100"), "kind": "focus", "tab": None},
        {"t": at("09:20:00.150"), "kind": "focus", "tab": 7, "window": 1, "url": learn, "title": "CS 136 Assignment 3"},
        {"t": at("09:21:00"), "kind": "idle", "state": "idle"},  # the extension's own minute: not used
        {"t": at("09:40:00"), "kind": "idle", "state": "active"},
    ])  # fmt: skip
    segments = build(vault, DAY, ZONE).segments
    assert spans(segments) == [("09:00:00", "09:20:00", "desk", "coding"), ("09:20:00", "09:50:00", "desk", "studying")]
    assert segments[0]["switches"] == 1 and segments[1]["project"] == "CS 136"
    assert segments[1]["doing"] == "CS 136 Assignment 3 (learn.uwaterloo.ca)"
    assert segments[1]["evidence"][0].startswith("capture:visit:")


def test_a_video_playing_on_is_watching_until_it_stops(vault: Vault) -> None:
    video = "https://www.youtube.com/watch?v=KyUTuwz_b7Q"
    tab = {"tab": 2, "window": 1, "url": video, "title": "Hash Tables - Computerphile - YouTube"}
    log(vault, "windows.jsonl", [
        {"t": at("14:00:00"), "kind": "window", "app": "opera.exe", "browser": True},
        away(at("14:05:05"), "idle", since=at("14:00:05")),
        {"t": at("14:30:00"), "kind": "window", "app": "opera.exe", "browser": True},
        away(at("14:40:00"), "locked"),
    ])  # fmt: skip
    log(vault, "browser.jsonl", [
        {"t": at("14:00:00.100"), "kind": "focus", **tab},
        {"t": at("14:00:05"), "kind": "media", **tab, "state": "playing", "position_s": 0, "duration_s": 900},
        {"t": at("14:20:00"), "kind": "media", **tab, "state": "paused", "position_s": 1195, "duration_s": 900},
    ])  # fmt: skip
    assert spans(build(vault, DAY, ZONE).segments) == [
        ("14:00:00", "14:20:00", "desk", "watching"),  # watching while it played
        ("14:20:00", "14:30:00", "away", None),
        ("14:30:00", "14:40:00", "desk", "watching"),
    ]


def test_the_phone_takes_over_away_time_but_not_desk_time(tmp_path: Path) -> None:
    scripted, vault = simulate(tmp_path / "v", "2026-10-28", 3, noise=False, phone=True)
    segments = build(vault, "2026-10-28", ZONE).segments
    places = [s for s in segments if s["where"].startswith("place:")]
    both = [s for s in places if s["source"] == "pc+phone"]
    assert both and all(any(e.startswith("capture:away:") for e in s["evidence"]) for s in both)
    assert all(any(e.startswith("place:") for e in s["evidence"]) for s in places)
    assert any(s["source"] == "phone" for s in places)  # the morning before the computer was used
    assert {s["source"] for s in segments if s["where"] == "desk"} == {"pc"}
    memos = [s for s in segments if s["talk"]]
    assert [s["where"] for s in memos] == [s["where"] for s in memos if "cafe" in s["where"]] and memos
    assert score(scripted, segments)["accuracy"] == 1.0


def test_write_keeps_the_contract_and_skips_empty_days(vault: Vault) -> None:
    log(vault, "windows.jsonl", [app(at("09:00:00"), "code.exe", CODE), away(at("09:30:00"), "locked")])
    report = write(vault, DAY, ZONE)
    path = vault.root / DAY / "timeline.jsonl"
    assert report["file"] == f"{DAY}/timeline.jsonl" and report["desk_s"] == 1800 and report["other_share"] == 0
    segments = read_lines(path)
    contracts.validate_all("timeline", segments)
    assert segments[0]["t"] == "2026-10-27T09:00:00-04:00"  # local, with its offset
    assert not vault.list_days() or DAY not in {d["date"] for d in vault.list_days()}  # not a vault day
    write(vault, "2026-10-29", ZONE)
    assert not (vault.root / "2026-10-29").exists()


# -- labels -------------------------------------------------------------------------------------------


def test_categories_come_from_corrections_rules_the_cache_and_one_model_call(vault: Vault) -> None:
    (vault.root / "categories.yaml").write_text("rules:\n  - category: coding\n    sites: [caesarzhou.com]\n", "utf-8")
    (vault.root / "label-corrections.json").write_text(json.dumps({
        "site:youtube.com": {"category": "studying"}, "page:https://x.com/home": {"project": "Launch"},
    }), "utf-8")  # fmt: skip
    (vault.root / CACHE_FILE).write_text(
        json.dumps({"labels": {"site:cached.example": {"category": "reading"}}}), "utf-8"
    )
    pages = {
        name: Activity.page(url, title)
        for name, url, title in [
            ("own", "https://caesarzhou.com/", "Caesar"), ("video", "https://www.youtube.com/watch?v=1", "A video"),
            ("x", "https://x.com/home", "Home / X"), ("cached", "https://cached.example/a", "A"),
            ("new", "https://unknown.example/a", "Some news"), ("odd", "https://weird.example/", "?"),
        ]
    }  # fmt: skip
    asked: list[list[dict]] = []

    def model(items: list[dict], categories: list[str]) -> dict[str, str]:
        asked.append(items)
        assert "coding" in categories and "other" in categories
        return {"site:unknown.example": "news", "site:weird.example": "not-a-category"}

    labels = Labels.load(vault)
    found = labels.label_all({a: 60.0 for a in pages.values()}, vault, model)
    got = {name: (found[a].category, found[a].project, found[a].by) for name, a in pages.items()}
    assert got == {
        "own": ("coding", None, "rule"),  # the vault's rule, before the built-in "other"
        "video": ("studying", None, "correction"),
        "x": ("social", "Launch", "rule"),  # a correction of the project alone
        "cached": ("reading", None, "cache"),
        "new": ("news", None, "llm"),
        "odd": ("other", None, "none"),  # an answer outside the categories is dropped
    }
    assert [[i["key"] for i in call] for call in asked] == [["site:unknown.example", "site:weird.example"]]
    assert asked[0][0] == {"key": "site:unknown.example", "name": "unknown.example", "titles": ["Some news"]}
    again = Labels.load(vault).label_all({a: 60.0 for a in pages.values()}, vault, model)
    assert len(asked) == 1 and again[pages["new"]].by == "cache"  # each app or site is asked once


def test_a_failed_model_call_is_reported_and_not_repeated_at_once(vault: Vault) -> None:
    def broken(items: list[dict], categories: list[str]) -> dict[str, str]:
        raise TimeoutError("no network")

    activity = {Activity.page("https://unknown.example/", "x"): 10.0}
    labels = Labels.load(vault)
    assert labels.label_all(activity, vault, broken)[next(iter(activity))].category == "other"
    assert labels.llm == "failed" and "no network" in labels.errors[0]
    later = Labels.load(vault)
    later.label_all(activity, vault, lambda *_: pytest.fail("asked again within the hour"))
    assert later.llm == "waiting"


def test_broken_label_files_are_reported_and_left_out(vault: Vault) -> None:
    (vault.root / "categories.yaml").write_text("rules:\n  - category:\n", "utf-8")
    (vault.root / "projects.yaml").write_text("projects: [not, a, mapping]\n", "utf-8")
    labels = Labels.load(vault)
    assert [e.split(" left out")[0] for e in labels.errors] == ["categories.yaml", "projects.yaml"]
    assert labels.category(Activity.app("code.exe", CODE)) == ("coding", "rule")  # the built-in rules stand


def test_projects_from_folders_courses_and_repositories(vault: Vault) -> None:
    assert editor_folder(Activity.app("code.exe", "● main.py - DAYCUBE - Visual Studio Code")) == "DAYCUBE"
    assert editor_folder(Activity.app("pycharm64.exe", "proj – main.py")) == "proj"
    assert editor_folder(Activity.app("code.exe", "Untitled-1 - Visual Studio Code")) is None
    assert repository(Activity.page("https://github.com/caesar/daycube/pull/1", "x")) == "daycube"
    assert repository(Activity.page("https://github.com/settings/profile", "x")) is None
    (vault.root / "projects.yaml").write_text(
        "projects:\n  DayCube:\n    folders: ['E:\\DAYCUBE']\n  Launch:\n    keywords: [launch plan]\n", "utf-8"
    )
    labels = Labels.load(vault)
    project = labels.project
    assert project(Activity.app("windowsterminal.exe", "PS E:\\DAYCUBE\\services>")) == "DayCube"
    assert project(Activity.app("notepad.exe", "E:\\DAYCUBE2\\a.txt")) is None
    assert project(Activity.page("https://github.com/caesar/daycube", "caesar/daycube")) == "DayCube"
    assert project(Activity.page("https://docs.google.com/d/1", "The launch plan - Google Docs")) == "Launch"
    assert project(Activity.app("code.exe", "a3.py - cs136 - Visual Studio Code")) == "CS 136"
    assert project(Activity.page("https://learn.uwaterloo.ca/x", "CS 136 Assignment 3")) == "CS 136"


# -- running ------------------------------------------------------------------------------------------


def test_the_librarian_writes_the_timeline_of_the_day_under_way(vault: Vault) -> None:
    from capture.librarian import Librarian

    log(vault, "windows.jsonl", [app(at("09:00:00"), "code.exe", CODE)])
    librarian = Librarian(vault.root, ZONE)
    noon = datetime.fromisoformat(at("12:00:00"))
    assert librarian.step_timeline(noon) == [DAY]
    segments = read_lines(vault.root / DAY / "timeline.jsonl")
    assert spans(segments) == [("09:00:00", "11:00:00", "desk", "coding")]  # in front since, at most 2 h a record
    assert librarian.step_timeline(noon + timedelta(minutes=10)) == [DAY]  # the day under way: every round
    morning = datetime.fromisoformat(at("09:00:00", "2026-10-28"))
    assert librarian.step_timeline(morning) == [DAY]  # finished: written once more, as recorded
    assert librarian.step_timeline(morning + timedelta(minutes=10)) == []  # and not again until a file changes


def test_the_doctor_reports_how_much_of_the_desk_no_category_knew(vault: Vault) -> None:
    from capture.doctor import PASS, WARN, _timeline

    now = datetime.fromisoformat(at("12:00:00", "2026-10-28"))
    assert _timeline(vault.root, TZ, now)[0][2].startswith("none written yet")
    log(vault, "windows.jsonl", [app(at("09:00:00"), "code.exe", CODE), app(at("10:00:00"), "mysteryapp.exe", "M"),
                                 away(at("10:20:00"), "locked")])  # fmt: skip
    write(vault, DAY, ZONE)
    [(verdict, name, detail)] = _timeline(vault.root, TZ, now)
    assert (verdict, name) == (WARN, "timeline") and "1.3 h at the desk, 25% in no category" in detail
    assert "mysteryapp: M" in detail
    (vault.root / "categories.yaml").write_text("rules:\n  - category: games\n    apps: [mysteryapp.exe]\n", "utf-8")
    write(vault, DAY, ZONE)
    assert _timeline(vault.root, TZ, now)[0][0] == PASS


def test_the_doctor_reports_yesterdays_review(vault: Vault) -> None:
    from capture.doctor import PASS, WARN, _review
    from ingest.review.summary import write_review
    from ingest.store import LifeStore

    now = datetime.fromisoformat(at("12:00:00", "2026-10-28"))
    log(vault, "windows.jsonl", [app(at("09:00:00"), "code.exe", CODE), away(at("09:30:00"), "locked")])
    [(verdict, _, detail)] = _review(vault.root, TZ, now)
    assert verdict == WARN and "daycube review --date yesterday" in detail
    write_review(vault, LifeStore(vault), DAY, ZONE, now=now)
    [(verdict, _, detail)] = _review(vault.root, TZ, now)
    assert verdict == PASS and detail == "yesterday (2026-10-27): final, written 2026-10-28 12:00 by template"

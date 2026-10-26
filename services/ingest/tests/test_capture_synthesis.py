"""Capture logs to activities and search units: what was in front, read, stared at, searched."""

import json

import pytest
from conftest import CAPTURE_DAY, DAY, capture_session
from ingest.ask.retrieve import retrieve
from ingest.ask.service import ask
from ingest.capture import capture_days, capture_units, read_capture
from ingest.capture.synthesize import has_capture_day
from ingest.store import LifeStore
from ingest.vault import Vault


def test_visits_follow_the_page_in_front_and_skip_idle_time(vault: Vault) -> None:
    capture_session(vault)
    day = read_capture(vault, CAPTURE_DAY)
    visits = [(v.title, round(v.seconds)) for v in day.visits]
    assert visits == [
        ("CS 136 Assignment 3 - Hash tables", 300),  # until the search replaced it in the same tab
        ("linear probing vs chaining - Google Search", 21),
        ("Hash table - Wikipedia", 399),  # until the session went idle
        ("Hash table - Wikipedia", 300),  # back after the break: a new visit
        ("Hash Tables - Computerphile - YouTube", 360),  # until the browser lost focus
    ]
    assert [m["seconds"] for m in day.media] == [300.0]
    assert [s["query"] for s in day.searches] == ["linear probing vs chaining"]


def test_every_kind_of_activity_becomes_a_unit(vault: Vault) -> None:
    capture_session(vault)
    units = capture_units(vault, CAPTURE_DAY)
    kinds = [u["type"] for u in units]
    assert sorted(set(kinds)) == [
        "web.click", "web.dwell", "web.look", "web.media", "web.read", "web.search", "web.selection", "web.visit",
    ]  # fmt: skip
    by = {u["type"]: u for u in units}
    assert by["web.read"]["text"].startswith("Read in CS 136 Assignment 3 - Hash tables (learn.uwaterloo.ca): Open")
    assert by["web.read"]["attention"] == 1.0 and by["web.read"]["confidence"] == 0.93
    assert "Stayed 16 s on this" in by["web.dwell"]["text"] and "October 30" in by["web.dwell"]["text"]
    assert by["web.look"]["text"].endswith("Assignment 3: Hash tables") and by["web.look"][
        "attention"
    ] == pytest.approx(3.92)
    assert by["web.click"]["text"] == (
        "Clicked “Hash table - Wikipedia” to en.wikipedia.org in linear probing vs chaining - Google Search (google.com)"
    )
    assert by["web.selection"]["text"] == "Copied in Hash table - Wikipedia (en.wikipedia.org): “Linear probing”"
    assert len({u["evidence_id"] for u in units}) == len(units)
    assert [u["t"] for u in units] == sorted(u["t"] for u in units)


def test_a_capture_only_day_is_searchable(vault: Vault) -> None:
    capture_session(vault)
    store = LifeStore(vault)
    assert capture_days(vault) == [CAPTURE_DAY] and CAPTURE_DAY in store.days()
    found = retrieve(store, "separate chaining bucket")
    assert found[0]["type"] == "web.read" and found[0]["day"] == CAPTURE_DAY
    assert found[0]["t"] == 9 * 3600 + 60 and found[0]["place"] is None  # 09:01 Toronto
    answer = ask(store, CAPTURE_DAY, "when is assignment 3 due")
    assert "October 30" in answer["sentences"][0]["text"]


def test_single_capture_day_check_matches_history_and_update_does_not_scan(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_session(vault)
    packed = vault.root / "capture" / "2026-10-20" / "browser.jsonl.zst"
    packed.parent.mkdir(parents=True)
    packed.touch()
    invalid = vault.root / "capture" / "2026-99-99" / "browser.jsonl"
    invalid.parent.mkdir(parents=True)
    invalid.touch()
    empty = vault.root / "capture" / "2026-10-19"
    empty.mkdir()
    dates = [CAPTURE_DAY, "2026-10-20", "2026-99-99", "2026-10-19", "../outside", "20261020"]
    assert {day for day in dates if has_capture_day(vault, day)} == set(capture_days(vault))

    def unexpected_scan(_: Vault) -> list[str]:
        raise AssertionError("updating a single day must not scan the whole history")

    monkeypatch.setattr("ingest.store.db.capture_days", unexpected_scan)
    assert LifeStore(vault).update(CAPTURE_DAY)["units"] > 0
    with pytest.raises(FileNotFoundError):
        LifeStore(vault).update("2026-10-19")
    with pytest.raises(ValueError):
        LifeStore(vault).update("2026-99-99")


def test_new_capture_lines_reindex_the_day(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("ingest.store.db.monotonic", lambda: clock[0])
    older = "2026-10-15"
    capture_session(vault)  # 2026-10-22, the newest day
    capture_session(vault, older)
    store = LifeStore(vault)
    assert not retrieve(store, "bloom filter")

    def append(day: str, id: str, query: str) -> None:
        with (vault.root / "capture" / day / "browser.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"id": id, "t": f"{day}T10:00:00-04:00", "kind": "search", "tab": 1, "window": 1,
                                     "url": "https://duckduckgo.com/?q=x", "title": "", "engine": "duckduckgo",
                                     "query": query}) + "\n")  # fmt: skip

    # The newest days, where records arrive, are checked on every search: found at once.
    append(CAPTURE_DAY, "s2-0000001", "bloom filter false positive rate")
    assert retrieve(store, "bloom filter")[0]["text"] == "Searched duckduckgo: bloom filter false positive rate"
    # Older days are rechecked after the documented two-second window; no real sleep in tests.
    append(older, "s3-0000001", "cuckoo hashing")
    assert not retrieve(store, "cuckoo")
    clock[0] = 2.01
    assert retrieve(store, "cuckoo")[0]["day"] == older


def test_a_vault_day_keeps_its_units_and_gains_its_capture(vault: Vault) -> None:
    capture_session(vault, DAY)
    store = LifeStore(vault)
    store.update(DAY)
    types = {r["type"] for r in retrieve(store, "linear probing", day=DAY, limit=50)}
    assert "web.selection" in types
    assert retrieve(store, "hinge inference loop", day=DAY)  # the sample day's own records

from __future__ import annotations

import json
import random
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from ingest.contracts import ContractError, validate
from ingest.store import LifeStore
from ingest.time import overlaps
from ingest.vault import EventSeries, Vault, atomic_text, read_json, read_lines

DAY = "2026-10-20"
BASE = datetime.fromisoformat("2026-10-20T08:00:00-04:00")


def synthetic_events(rng: random.Random, count: int) -> list[dict]:
    events = []
    for index in range(count):
        start = BASE + timedelta(minutes=rng.randrange(0, 14 * 60))
        record = {"id": f"e{index}", "t": start.isoformat(), "type": "screen.app"}
        roll = rng.random()
        if roll < 0.6:  # most events have a duration, a few span hours
            minutes = rng.choice([1, 5, 20, 90, 400]) if roll < 0.55 else 0
            record["t_end"] = (start + timedelta(minutes=minutes)).isoformat()
        events.append(record)
    return events


def test_event_window_matches_a_full_scan() -> None:
    rng = random.Random(7)
    events = synthetic_events(rng, 400)
    series = EventSeries.build(events)
    for _ in range(300):
        t0 = BASE + timedelta(minutes=rng.randrange(-60, 15 * 60))
        t1 = t0 + timedelta(minutes=rng.choice([1, 15, 60, 300]))
        expected = [e for e in series.records if overlaps(e["t"], e.get("t_end"), t0.isoformat(), t1.isoformat())]
        assert series.window(t0, t1) == expected


def test_series_order_is_stable_for_equal_start_times() -> None:
    events = [{"id": name, "t": BASE.isoformat()} for name in "cab"]
    events.insert(1, {"id": "early", "t": (BASE - timedelta(seconds=1)).isoformat()})
    assert [e["id"] for e in EventSeries.build(events).records] == ["early", "c", "a", "b"]


def test_api_window_filters_by_type(vault: Vault) -> None:
    every = vault.events_in(DAY, "2026-10-20T00:00:00-04:00", "2026-10-21T00:00:00-04:00", None)
    assert len(every) == 14
    quotes = vault.events_in(DAY, "2026-10-20T00:00:00-04:00", "2026-10-21T00:00:00-04:00", "said.quote")
    assert quotes and all(e["type"] == "said.quote" for e in quotes)
    with pytest.raises(ValueError, match="both"):
        vault.events_in(DAY, "2026-10-20T00:00:00-04:00", None, None)


def test_validation_is_memoized_until_a_file_changes(vault: Vault) -> None:
    vault.validate(DAY)
    path = vault.day_path(DAY) / "graph.json"
    graph = read_json(path)
    graph["edges"].append({"from": "missing", "to": "missing", "rel": "with", "evidence": []})
    # An outside editor, not the vault: only the file stamp can reveal the change.
    atomic_text(path, json.dumps(graph))
    with pytest.raises(ValueError, match="dangling"):
        vault.validate(DAY)


def test_same_size_rewrite_through_the_vault_is_never_stale(vault: Vault) -> None:
    path = vault.day_path(DAY) / "day.json"
    before = vault.load_day(DAY)
    data = read_json(path)
    data["places"].reverse()  # same bytes, different order; may land within one clock tick
    vault.write_json(path, data)
    assert [p["id"] for p in vault.snapshot(DAY).day["places"]] == [p["id"] for p in data["places"]]
    assert vault.load_day(DAY)["layers"] == before["layers"]


def test_appending_events_keeps_a_valid_day_validated(vault: Vault) -> None:
    vault.validate(DAY)
    event = dict(vault.events(DAY)[0], id="ev_appended")
    vault.append_events(DAY, [event])
    assert vault._validated.get(DAY) == vault.validation_key(vault.snapshot(DAY))
    assert Vault(vault.root).validate(DAY)["events"] == 15  # a cold check agrees


def test_returned_events_are_copies(vault: Vault) -> None:
    vault.events(DAY)[0]["label"] = "changed by a caller"
    assert vault.events(DAY)[0]["label"] != "changed by a caller"


def test_day_payload_replays_until_names_change(vault: Vault) -> None:
    first = vault.day_payload(DAY)
    assert json.loads(first) == json.loads(json.dumps(vault.load_day(DAY)))
    assert vault.day_payload(DAY) is first
    vault.write_json(vault.root / "places.json", {"hall": "Observatory"})
    layers = json.loads(vault.day_payload(DAY))["layers"]
    assert next(layer for layer in layers if layer["placeId"] == "hall")["name"] == "Observatory"


def test_snapshot_rejects_records_that_break_their_schema(vault: Vault) -> None:
    path = vault.day_path(DAY) / "events.jsonl"
    events = read_lines(path)
    events[3]["confidence"] = 7
    vault.write_lines(path, events)
    with pytest.raises(ContractError) as error:
        vault.load_day(DAY)
    assert error.value.contract == "event"
    assert error.value.path == ["confidence"]


def test_contract_errors_locate_nested_values() -> None:
    with pytest.raises(ContractError) as error:
        validate("skeleton", {"type": "FeatureCollection", "features": [{"type": "Line"}]})
    assert error.value.path[:2] == ["features", 0]


def test_index_stores_moment_offsets(vault: Vault, tmp_path: Path) -> None:
    store = LifeStore(vault)
    store.update(DAY)
    with closing(sqlite3.connect(store.path)) as db:
        rows = db.execute("SELECT evidence_id, offset_s FROM units WHERE moment_id IS NOT NULL").fetchall()
    assert rows and all(offset is not None and offset >= 0 for _, offset in rows)

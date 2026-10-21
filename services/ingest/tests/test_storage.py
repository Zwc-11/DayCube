"""Caches, appends and compact representations: each one must equal the slow, obvious version."""

from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ingest.cache import SegmentedLRU
from ingest.index.build import collect_units, ocr_runs
from ingest.trajectory import simplify
from ingest.vault import Vault, append_text, read_lines

DAY = "2026-10-20"


# -- segmented LRU ----------------------------------------------------------------------------


def test_lru_evicts_by_size_and_skips_oversized_entries() -> None:
    cache: SegmentedLRU[str, str] = SegmentedLRU(10)
    for key in "abcd":
        cache.put(key, key, 3)
    assert "a" not in cache and cache.size == 9
    cache.put("huge", "x", 11)
    assert "huge" not in cache
    cache.put("b", "b2", 1)  # replacing an entry re-charges its size
    assert cache.size == 7 and cache.get("b") == "b2"


def test_lru_keeps_reused_days_through_a_scan() -> None:
    cache: SegmentedLRU[str, str] = SegmentedLRU(10)
    cache.put("today", "t", 3)
    assert cache.get("today") == "t"  # second access: protected
    for day in range(20):  # a pass over every other day, each touched once
        cache.put(f"day{day}", "d", 3)
    assert cache.get("today") == "t"
    assert cache.size <= 10


def test_lru_demotes_protected_overflow_before_evicting_it() -> None:
    cache: SegmentedLRU[str, int] = SegmentedLRU(10, protected_share=0.5)
    for key in "abc":
        cache.put(key, 0, 2)
        cache.get(key)
    assert cache._sizes[1] <= 5 and len(cache) == 3  # 'a' went back to probation, not out


def test_snapshot_memory_stays_within_budget(vault: Vault) -> None:
    budget = 3 * vault.snapshot(DAY).source_bytes // 2
    vault._snapshots = SegmentedLRU(budget)
    for offset in range(1, 6):
        other = f"2026-10-{20 + offset}"
        destination = vault.day_path(other, must_exist=False)
        destination.mkdir()
        data = json.loads((vault.day_path(DAY) / "day.json").read_text())
        (destination / "day.json").write_text(json.dumps(dict(data, date=other)))
        vault.snapshot(other)
    assert vault._snapshots.size <= budget


# -- trajectory simplification ----------------------------------------------------------------


def walk(rng: random.Random, count: int) -> list[dict]:
    points, x, y, t = [], 0.0, 0.0, 0.0
    for _ in range(count):
        if rng.random() < 0.7:  # dwell, with sub-tolerance jitter now and then
            jitter = rng.choice([0.0, 0.0, 0.03])
            points.append({"t": t, "x": x + jitter, "y": y, "floor": 0, "fidelity": "estimated", "pos_conf": 0.4})
        else:
            x, y = x + rng.uniform(-2, 2), y + rng.uniform(-2, 2)
            points.append({"t": t, "x": x, "y": y, "floor": 0, "fidelity": "estimated", "pos_conf": 0.4})
        t += rng.choice([1.0, 1.0, 5.0])
    return points


def position_at(path: list[dict], t: float) -> tuple[float, float]:
    for a, b in zip(path, path[1:], strict=False):
        if a["t"] <= t <= b["t"]:
            r = (t - a["t"]) / (b["t"] - a["t"]) if b["t"] > a["t"] else 0.0
            return a["x"] + r * (b["x"] - a["x"]), a["y"] + r * (b["y"] - a["y"])
    return path[-1]["x"], path[-1]["y"]


@pytest.mark.parametrize("seed", range(5))
def test_simplified_path_stays_within_tolerance_at_every_fix_time(seed: int) -> None:
    points = walk(random.Random(seed), 600)
    simple = simplify(points, 0.1)
    assert simple[0] is points[0] and simple[-1] is points[-1]
    assert len(simple) < len(points)
    for p in points:
        x, y = position_at(simple, p["t"])
        assert math.hypot(x - p["x"], y - p["y"]) <= 0.1 + 1e-9


def test_a_pause_on_a_straight_line_survives_simplification() -> None:
    """Dwell, walk east at 1 m/s, dwell: four corners. A time-blind simplifier would keep only the
    two ends, and the character would glide across the whole 700 s instead of pausing."""
    points = [{"t": float(t), "x": min(max(t - 300, 0), 100) * 1.0, "y": 0.0} for t in range(701)]
    for p in points:
        p.update(floor=0, fidelity="estimated", pos_conf=0.4)
    assert [p["t"] for p in simplify(points, 0.1)] == [0.0, 300.0, 400.0, 700.0]


def test_simplification_keeps_floor_changes_and_unknown_positions() -> None:
    points = [{"t": float(t), "x": 1.0, "y": 1.0, "floor": 0, "fidelity": "known", "pos_conf": 0.9} for t in range(10)]
    points[4] = dict(points[4], x=None, y=None)
    for p in points[7:]:
        p["floor"] = 2
    kept = [p["t"] for p in simplify(points, 0.1)]
    assert {3.0, 4.0, 5.0, 6.0, 7.0} <= set(kept)


# -- append-only logs -------------------------------------------------------------------------


def test_append_cuts_a_torn_tail_and_keeps_a_record_missing_its_newline(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    log.write_text('{"a": 1}\n{"b": 2')  # crash mid-write
    assert read_lines(log) == [{"a": 1}]
    append_text(log, '{"c": 3}\n')
    assert read_lines(log) == [{"a": 1}, {"c": 3}]
    edited = tmp_path / "edited.jsonl"
    edited.write_text('{"a": 1}\n{"b": 2}')  # a valid last record without its newline
    append_text(edited, '{"c": 3}\n')
    assert read_lines(edited) == [{"a": 1}, {"b": 2}, {"c": 3}]


def test_append_writes_only_the_new_lines_and_matches_a_cold_read(vault: Vault) -> None:
    log = vault.day_path(DAY) / "events.jsonl"
    before = log.read_bytes()
    vault.validate(DAY)
    rng = random.Random(3)
    added = []
    for i in range(5):
        event = copy.deepcopy(rng.choice(vault.events(DAY)))
        added.append(dict(event, id=f"ev_append_{i}"))
    vault.append_events(DAY, added[:2])
    vault.append_events(DAY, added[2:])
    assert log.read_bytes().startswith(before)
    warm = vault.snapshot(DAY)
    cold = Vault(vault.root).snapshot(DAY)
    assert warm.events.records == cold.events.records
    assert warm.events.starts == cold.events.starts and warm.events.max_span == cold.events.max_span
    assert warm.day == cold.day
    assert vault.validate(DAY) == Vault(vault.root).validate(DAY)


# -- payloads, listings and the index ---------------------------------------------------------


def test_day_responses_revalidate_with_their_entity_tag(client: TestClient, auth: dict[str, str]) -> None:
    first = client.get(f"/day/{DAY}")
    tag = first.headers["etag"]
    again = client.get(f"/day/{DAY}", headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.content == b""
    client.post("/places/rename", json={"place_id": "hall", "name": "Observatory"}, headers=auth)
    changed = client.get(f"/day/{DAY}", headers={"If-None-Match": tag})
    assert changed.status_code == 200 and changed.headers["etag"] != tag


def test_day_list_carries_place_kinds_in_visit_order(vault: Vault) -> None:
    (entry,) = vault.list_days()
    places = sorted(vault.snapshot(DAY).day["places"], key=lambda p: p["layer_index"])
    assert entry["kinds"] == [p["kind"] for p in places]
    assert vault.list_days()[0] is entry  # unchanged day.json: no re-read


def test_ocr_runs_collapse_repeats_and_split_on_gaps() -> None:
    def shot(t: int, texts: list[str], attention: float | None = 0.5) -> dict:
        blocks = [{"id": f"b{i}", "text": s, "attention": attention, "conf": 0.9} for i, s in enumerate(texts)]
        return {"shot": f"s{t}", "t": f"2026-10-20T09:{t:02d}:00-04:00", "title": "Doc", "blocks": blocks}

    shots = [shot(0, ["a", "b"]), shot(1, ["a", "b"], 0.9), shot(2, ["b"]), shot(3, ["a"])]
    runs = [(s["shot"], b["text"], last[14:16]) for s, b, last in ocr_runs(shots)]
    assert runs == [("s1", "a", "01"), ("s1", "b", "02"), ("s3", "a", "03")]


def test_repeated_screens_index_once(vault: Vault) -> None:
    path = vault.day_path(DAY) / "screens/ocr.jsonl"
    shots = read_lines(path)
    later = dict(shots[-1], shot="1005_30", t=shots[-1]["t"].replace(":05:00", ":05:30"))
    vault.write_lines(path, [*shots, later])
    ocr = [u for u in collect_units(vault, DAY) if u["type"] == "ocr.block"]
    texts = [u["text"] for u in ocr]
    assert len(texts) == len(set(texts))
    assert any(u["t_end"] == later["t"] for u in ocr)


# -- compaction -------------------------------------------------------------------------------


def test_compacted_day_reads_and_indexes_identically(vault: Vault) -> None:
    from ingest.index.build import build_index

    path = vault.day_path(DAY)
    for _ in range(40):  # make the log big enough to compact
        vault.append_events(DAY, [dict(copy.deepcopy(vault.events(DAY)[0]), id=f"ev_bulk_{_}")])
    payload, events = vault.day_payload(DAY), vault.events(DAY)
    units = build_index(vault, DAY)["units"]
    report = vault.compact_day(DAY)
    assert "2026-10-20/events.jsonl" in report and not (path / "events.jsonl").exists()
    for reader in (vault, Vault(vault.root)):
        assert reader.day_payload(DAY) == payload and reader.events(DAY) == events
        assert reader.validate(DAY)["valid"]
        assert build_index(reader, DAY)["units"] == units
    vault.append_events(DAY, [dict(copy.deepcopy(events[0]), id="ev_after_compact")])
    assert (path / "events.jsonl").exists() and not (path / "events.jsonl.zst").exists()
    assert len(Vault(vault.root).events(DAY)) == len(events) + 1

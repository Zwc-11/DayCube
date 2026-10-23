"""Each test pins a way a warm cache once diverged from a cold read of the same files."""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

import ingest.vault as vault_module
import pytest
from fastapi.testclient import TestClient
from ingest.api import create_app
from ingest.ask.retrieve import retrieve
from ingest.config import PROJECT_ROOT, Settings
from ingest.contracts import ContractError
from ingest.store import LifeStore
from ingest.vault import Vault, atomic_text, read_json, read_lines

DAY = "2026-10-20"


@pytest.fixture
def venue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A writable copy of the venue packs; venues live outside the day folder."""
    project = tmp_path / "project"
    shutil.copytree(PROJECT_ROOT / "venues", project / "venues")
    monkeypatch.setattr(vault_module, "PROJECT_ROOT", project)
    return project / "venues/engineering-hall.json"


def test_validation_memo_sees_venue_edits(vault: Vault, venue: Path) -> None:
    warm = Vault(vault.root)
    assert warm.validate(DAY)["valid"]
    atomic_text(venue, json.dumps(dict(read_json(venue), floor_height_m=-1)))
    with pytest.raises(ContractError):
        warm.validate(DAY)
    with pytest.raises(ContractError):
        LifeStore(warm).update(DAY)


def test_stray_files_in_moments_are_ignored(vault: Vault) -> None:
    (vault.day_path(DAY) / "moments/.DS_Store").write_bytes(b"\0")
    vault.validate(DAY)
    with TestClient(create_app(Settings(vault_root=vault.root, vector_search=False, _env_file=None))) as client:
        assert client.get(f"/day/{DAY}").status_code == 200


def test_returned_events_share_nothing_with_the_cache(vault: Vault) -> None:
    first = vault.events(DAY)[0]
    reference = first["source"]["ref"]
    first["source"]["ref"] = "edited by a caller"
    vault.append_events(DAY, [dict(copy.deepcopy(vault.events(DAY)[0]), id="ev_new")])
    on_disk = next(e for e in read_lines(vault.day_path(DAY) / "events.jsonl") if e["id"] == first["id"])
    assert on_disk["source"]["ref"] == reference


def test_loaded_day_shares_nothing_with_the_cache(vault: Vault) -> None:
    vault.validate(DAY)
    day = vault.load_day(DAY)
    day["cube_faces"].append("nope")
    next(layer for layer in day["layers"] if layer["placeId"] == "hall")["geometry"]["levels"].clear()
    again = vault.load_day(DAY)
    assert "nope" not in again["cube_faces"]
    assert next(layer for layer in again["layers"] if layer["placeId"] == "hall")["geometry"]["levels"]
    vault.append_events(DAY, [dict(copy.deepcopy(vault.events(DAY)[0]), id="ev_extra")])
    assert "nope" not in read_json(vault.day_path(DAY) / "day.json")["cube_faces"]


def test_validity_is_not_carried_over_a_concurrent_write(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    vault.validate(DAY)
    path = vault.day_path(DAY) / "events.jsonl"
    invalidate = Vault.invalidate_index

    def racing_invalidate(self: Vault, day: str) -> None:
        invalidate(self, day)
        lines = read_lines(path)  # another process appends a duplicate id inside the window
        atomic_text(path, "".join(json.dumps(r) + "\n" for r in [*lines, lines[0]]))

    monkeypatch.setattr(Vault, "invalidate_index", racing_invalidate)
    vault.append_events(DAY, [dict(copy.deepcopy(vault.events(DAY)[0]), id="ev_extra")])
    monkeypatch.setattr(Vault, "invalidate_index", invalidate)
    with pytest.raises(ValueError):
        vault.validate(DAY)


def test_same_size_rename_within_one_clock_tick_reindexes(vault: Vault) -> None:
    names = vault.root / "places.json"
    vault.write_json(names, {"cafe1": "Zephyr Hut"})
    store = LifeStore(vault)
    store.ensure([DAY])
    before = os.stat(names)
    vault.write_json(names, {"cafe1": "Quasar Hut"})
    os.utime(names, ns=(before.st_atime_ns, before.st_mtime_ns))  # a coarse filesystem clock
    hits = retrieve(store, "Quasar", day=DAY)
    assert hits and hits[0]["place"] == "cafe1"


def test_rename_ignores_unrelated_invalid_days(vault: Vault) -> None:
    other = vault.root / "2026-10-19"
    other.mkdir()
    atomic_text(other / "day.json", json.dumps({"date": "2026-10-19", "places": [], "stats": {}}))
    assert vault.has_place("hall")

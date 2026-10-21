import pytest
from ingest.contracts import validate
from ingest.time import tod
from ingest.vault import read_json
from jsonschema import ValidationError

DAY = "2026-10-20"


def test_complete_fixture(vault):
    assert vault.validate(DAY) == {"date": DAY, "valid": True, "events": 14, "moments": 1}
    assert len(read_json(vault.day_path(DAY) / "skeleton.geojson")["features"]) == 60


@pytest.mark.parametrize("path", ["../elsewhere", "../../.token", "2026-02-30", "sample"])
def test_day_path_rejects_traversal_and_invalid_dates(vault, path):
    with pytest.raises(ValueError):
        vault.day_path(path)


def test_seeding_never_overwrites_existing_day(vault):
    with pytest.raises(FileExistsError):
        vault.seed()


def test_conditional_event_fields_are_enforced(vault):
    event = next(e for e in vault.events(DAY) if e["type"] == "said.quote")
    event.pop("speaker")
    with pytest.raises(ValidationError):
        validate("event", event)


def test_viewer_times_use_local_seconds(vault):
    day = vault.load_day(DAY)
    assert day["layers"][0]["t0"] == 8 * 3600
    assert tod("2026-10-20T16:40:05Z", "America/Toronto") == 45605


def test_missing_moment_is_rejected_before_writing(vault):
    event = dict(
        vault.events(DAY)[0], id="missing-moment", type="moment.video", moment_id="unknown"
    )
    with pytest.raises(ValueError, match="missing moment"):
        vault.append_events(DAY, [event])
    assert len(vault.events(DAY)) == 14


def test_layers_keep_visit_order_names_and_unlocated_records_separate(vault):
    data = vault.load_day(DAY)
    source = read_json(vault.day_path(DAY) / "day.json")
    source["places"].reverse()
    vault.write_json(vault.day_path(DAY) / "day.json", source)
    vault.write_json(vault.root / "places.json", {"cafe1": "Renamed café"})
    unlocated = dict(vault.events(DAY)[0], id="unlocated", place=None)
    vault.append_events(DAY, [unlocated])
    skeleton = read_json(vault.day_path(DAY) / "skeleton.geojson")
    skeleton["features"].append(
        dict(
            skeleton["features"][0],
            properties=dict(skeleton["features"][0]["properties"], place=None),
        )
    )
    vault.write_json(vault.day_path(DAY) / "skeleton.geojson", skeleton)
    layers = vault.load_day(DAY)["layers"]
    assert [layer["placeId"] for layer in layers] == [layer["placeId"] for layer in data["layers"]]
    assert layers[1]["name"] == "Renamed café"
    assert sum(len(layer["path"]) for layer in layers) == 60
    assert sum(len(layer["events"]) for layer in layers) == 14
    for layer, original in zip(layers, data["layers"], strict=True):
        assert layer["events"] == original["events"]
        assert layer["path"] == original["path"]
        assert layer["quotes"] == original["quotes"]
        assert layer["moments"] == original["moments"]

import json
import os
import re
import secrets
import shutil
import tempfile
from collections import defaultdict
from datetime import date as Date
from pathlib import Path
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from . import contracts
from .config import PROJECT_ROOT
from .time import instant, tod


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".daycube-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


class Vault:
    """One writer process; atomic replacements prevent partially read JSON files."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.lock = RLock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        token_path = self.root / ".token"
        try:
            with token_path.open("x", encoding="utf-8") as stream:
                stream.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
        if not (self.root / "places.json").exists():
            self.write_json(self.root / "places.json", {})

    @property
    def token(self) -> str:
        return (self.root / ".token").read_text(encoding="utf-8").strip()

    def day_path(self, value: str, *, must_exist: bool = True) -> Path:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Date must be YYYY-MM-DD")
        Date.fromisoformat(value)
        path = self.safe_path(value)
        if must_exist and not (path / "day.json").is_file():
            raise FileNotFoundError(f"No vault day: {value}")
        return path

    def safe_path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("Path escapes the vault")
        return candidate

    def write_json(self, path: Path, value: Any) -> None:
        with self.lock:
            atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def write_lines(self, path: Path, values: list[dict]) -> None:
        with self.lock:
            atomic_text(path, "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values))

    def seed(self) -> str:
        fixture = PROJECT_ROOT / "fixtures/sample-day"
        day = read_json(fixture / "day.json")["date"]
        destination = self.day_path(day, must_exist=False)
        with self.lock:
            if destination.exists():
                raise FileExistsError(f"Day {day} already exists; seed never overwrites a day")
            shutil.copytree(fixture, destination)
        self.validate(day)
        return day

    def list_days(self) -> list[dict]:
        result = []
        for path in sorted(self.root.glob("????-??-??/day.json"), reverse=True):
            day = read_json(path)
            result.append({"date": day["date"], "stats": day["stats"]})
        return result

    def events(self, day: str) -> list[dict]:
        return sorted(
            read_lines(self.day_path(day) / "events.jsonl"), key=lambda e: instant(e["t"])
        )

    def append_events(self, day: str, events: list[dict]) -> int:
        with self.lock:
            path = self.day_path(day)
            data = read_json(path / "day.json")
            places = {p["id"] for p in data["places"]}
            for event in events:
                self.validate_event(event, day, data["tz"], places)
                if event.get("moment_id"):
                    moment_id = event["moment_id"]
                    if (
                        not re.fullmatch(r"[A-Za-z0-9_-]+", moment_id)
                        or not (path / "moments" / moment_id / "meta.json").is_file()
                    ):
                        raise ValueError("Event references a missing moment")
            current = self.events(day)
            known_ids = {e["id"] for e in current}
            incoming = [e["id"] for e in events]
            if len(set(incoming)) != len(incoming) or known_ids.intersection(incoming):
                raise ValueError("Event ids must be unique within a day")
            self.write_lines(path / "events.jsonl", current + events)
            self.refresh_stats(day)
            self.invalidate_index(day)
        return len(events)

    @staticmethod
    def validate_event(event: dict, day: str, timezone: str, places: set[str]) -> None:
        contracts.validate("event", event)
        if instant(event["t"]).astimezone(ZoneInfo(timezone)).date().isoformat() != day:
            raise ValueError("Event timestamp is outside the selected local day")
        if event.get("t_end") and instant(event["t_end"]) < instant(event["t"]):
            raise ValueError("Event ends before it starts")
        if event.get("place") and event["place"] not in places:
            raise ValueError("Event references an unknown place")
        if (event.get("x") is None) != (event.get("y") is None):
            raise ValueError("x and y must both be known or both be null")

    def invalidate_index(self, day: str) -> None:
        (self.day_path(day) / "index.stamp.json").unlink(missing_ok=True)

    def refresh_stats(self, day: str) -> None:
        path = self.day_path(day)
        data = read_json(path / "day.json")
        events = self.events(day)
        data["stats"] = {
            "events": len(events),
            "moments": len(list((path / "moments").glob("*/meta.json"))),
            "places": len(data["places"]),
            "floor_changes": sum(e["type"] == "place.floor" for e in events),
        }
        self.write_json(path / "day.json", data)

    def validate(self, day: str) -> dict:
        path = self.day_path(day)
        data = read_json(path / "day.json")
        contracts.validate("day", data)
        if data["date"] != day:
            raise ValueError("Folder date differs from day.json")
        ZoneInfo(data["tz"])
        places = {p["id"] for p in data["places"]}
        if len(places) != len(data["places"]):
            raise ValueError("Duplicate place visit ids")
        layer_ids = [p["layer_index"] for p in data["places"]]
        if len(set(layer_ids)) != len(layer_ids):
            raise ValueError("Duplicate layer indices")
        for place in data["places"]:
            if instant(place["t1"]) <= instant(place["t0"]):
                raise ValueError("Visit must end after it starts")
            if place.get("venue"):
                venue = (PROJECT_ROOT / place["venue"]).resolve()
                if not venue.is_relative_to(PROJECT_ROOT / "venues"):
                    raise ValueError("Venue path must stay in venues/")
                contracts.validate("venue", read_json(venue))
        skeleton = read_json(path / "skeleton.geojson")
        contracts.validate("skeleton", skeleton)
        for feature in skeleton["features"]:
            properties = feature["properties"]
            instant(properties["t"])
            if properties["place"] is not None and properties["place"] not in places:
                raise ValueError("Skeleton references an unknown place")
        events = self.events(day)
        ids = {e["id"] for e in events}
        if len(ids) != len(events):
            raise ValueError("Duplicate event ids")
        moments = list((path / "moments").glob("*/meta.json"))
        moment_ids = {m.parent.name for m in moments}
        for event in events:
            self.validate_event(event, day, data["tz"], places)
            if event.get("moment_id") and event["moment_id"] not in moment_ids:
                raise ValueError("Event references a missing moment")
        if not set(data["cube_faces"]) <= ids | moment_ids:
            raise ValueError("Cube faces reference missing records")
        for record in read_lines(path / "screens/ocr.jsonl"):
            contracts.validate("ocr", record)
        for meta in moments:
            moment = read_json(meta)
            contracts.validate("moment", moment)
            if moment["id"] != meta.parent.name or (
                moment["place"] is not None and moment["place"] not in places
            ):
                raise ValueError("Moment id/place does not match its vault location")
            transcript = read_json(meta.parent / "transcript.json")
            contracts.validate("transcript", transcript)
            for segment in transcript["segments"]:
                if not 0 <= segment["start"] < segment["end"] <= moment["duration_s"]:
                    raise ValueError("Transcript segment lies outside its moment")
        graph = read_json(path / "graph.json")
        contracts.validate("graph", graph)
        nodes = {node["id"] for node in graph["nodes"]}
        if len(nodes) != len(graph["nodes"]) or any(
            edge["from"] not in nodes or edge["to"] not in nodes for edge in graph["edges"]
        ):
            raise ValueError("Graph contains duplicate nodes or dangling edges")
        expected = {
            "events": len(events),
            "moments": len(moments),
            "places": len(places),
            "floor_changes": sum(e["type"] == "place.floor" for e in events),
        }
        if data["stats"] != expected:
            raise ValueError("Day stats differ from the vault records")
        return {"date": day, "valid": True, "events": len(events), "moments": len(moments)}

    def load_day(self, day: str) -> dict:
        path = self.day_path(day)
        data = read_json(path / "day.json")
        names = read_json(self.root / "places.json")
        events = self.events(day)
        fixes = read_json(path / "skeleton.geojson")["features"]
        events_by_place: dict[str | None, list[dict]] = defaultdict(list)
        fixes_by_place: dict[str | None, list[dict]] = defaultdict(list)
        for event in events:
            events_by_place[event.get("place")].append(event)
        for fix in fixes:
            fixes_by_place[fix["properties"]["place"]].append(fix["properties"])
        layers = []
        for place in sorted(data["places"], key=lambda p: p["layer_index"]):
            name = names.get(place["id"], place["name"])
            pins = [dict(e, tod=tod(e["t"], data["tz"])) for e in events_by_place[place["id"]]]
            venue = read_json(PROJECT_ROOT / place["venue"]) if place.get("venue") else None
            layers.append(
                {
                    "index": place["layer_index"],
                    "placeId": place["id"],
                    "name": name,
                    "kind": place["kind"],
                    "t0": tod(place["t0"], data["tz"]),
                    "t1": tod(place["t1"], data["tz"]),
                    "floors": place["floors_visited"],
                    "floorHeight": venue["floor_height_m"] if venue else 3.3,
                    "frame": {
                        "lat": place["lat"],
                        "lon": place["lon"],
                        "axisDeg": venue["axis_deg"] if venue else 0,
                        "size": [20, 14],
                    },
                    "geometry": {
                        "fidelity": "illustrative",
                        "footprint": [[-10, -7], [10, -7], [10, 7], [-10, 7]],
                        "levels": venue["levels"] if venue else [],
                    },
                    "path": [
                        dict(f, t=tod(f["t"], data["tz"])) for f in fixes_by_place[place["id"]]
                    ],
                    "events": pins,
                    "quotes": [e for e in pins if e["type"] == "said.quote"],
                    "moments": sorted({e["moment_id"] for e in pins if e.get("moment_id")}),
                }
            )
        return {**data, "layers": layers}

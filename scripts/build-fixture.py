"""Reproduce the checked-in, entirely fictional sample day. Never writes to vault/."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/sample-day"


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def lines(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")


def stamp(time: str) -> str:
    return f"2026-10-20T{time}-04:00"


def main() -> None:
    places = []
    for index, (key, name, kind, start, end, floors) in enumerate(
        [
            ("home1", "Home · morning", "house", "08:00:00", "08:45:00", [0]),
            ("cafe1", "Maple Café", "cafe", "09:00:00", "09:30:00", [0]),
            ("hall", "Engineering Hall", "venue", "10:00:00", "12:00:00", [0, 2]),
            ("cafe2", "Maple Café · lunch", "cafe", "12:30:00", "13:15:00", [0]),
            ("home2", "Home · evening", "house", "14:00:00", "18:00:00", [0]),
        ]
    ):
        places.append(
            {
                "id": key,
                "name": name,
                "kind": kind,
                "t0": stamp(start),
                "t1": stamp(end),
                "lat": 43.66 + index * 0.001,
                "lon": -79.39 + index * 0.001,
                "osm_id": None,
                "venue": "venues/engineering-hall.json" if key == "hall" else None,
                "floors_visited": floors,
                "conf": 0.7,
                "layer_index": index,
            }
        )
    events = []

    def event(key: str, time: str, type: str, label: str, place: str, **extras: Any) -> None:
        events.append(
            {
                "id": key,
                "t": stamp(time),
                "type": type,
                "label": label,
                "place": place,
                "floor": 2 if place == "hall" else 0,
                "x": None,
                "y": None,
                "position_fidelity": "illustrative",
                "source": {"kind": "fixture", "ref": "fixture:synthetic"},
                "confidence": 0.7,
                "fidelity": "illustrative",
                "attention": None,
                "people": [],
                "topics": [],
                "text": "",
                **extras,
            }
        )

    event("ev_001", "08:00:00", "place.visit", "Morning at home", "home1", conf=0.7)
    event(
        "ev_002",
        "08:15:00",
        "screen.app",
        "Planned the day",
        "home1",
        app="notes.exe",
        window_title="Day plan",
        t_end=stamp("08:25:00"),
        text="Prepare the DAYCUBE fixture and demo.",
    )
    event("ev_003", "09:00:00", "place.visit", "Coffee at Maple Café", "cafe1", conf=0.7)
    event(
        "ev_004",
        "09:10:00",
        "screen.page",
        "Read the project notes",
        "cafe1",
        url="https://example.com/daycube",
        title="DAYCUBE project notes",
        scroll_depth=0.7,
        t_end=stamp("09:20:00"),
        text="Fixture first. The vault is the integration contract.",
    )
    event("ev_005", "10:00:00", "place.visit", "Arrived at Engineering Hall", "hall", conf=0.7, floor=0)
    event("ev_006", "10:02:00", "place.floor", "Reached the second floor", "hall", conf=0.7)
    event(
        "ev_007",
        "10:05:00",
        "screen.read",
        "Read pull request 12",
        "hall",
        blocks=["b0", "b1"],
        attention=0.71,
        t_end=stamp("10:16:00"),
        text="Add hinge inference loop. Review the API contracts before merging.",
        source={"kind": "fixture", "ref": "screens/1005_00.svg"},
    )
    event("ev_008", "12:30:00", "place.visit", "Lunch at Maple Café", "cafe2", conf=0.7)
    event("ev_009", "12:40:00", "moment.audio", "Planning the demo with Sam", "cafe2", moment_id="mo_001")
    event("ev_010", "14:00:00", "place.visit", "Returned home", "home2", conf=0.7)
    event(
        "ev_011",
        "15:00:00",
        "screen.app",
        "Built the viewer",
        "home2",
        app="code.exe",
        window_title="DAYCUBE viewer",
        t_end=stamp("15:30:00"),
        text="Connect the viewer to the local API.",
    )
    segments = [
        {
            "start": 5,
            "end": 10,
            "text": "We decided the demo order: open the cube, ask a question, then enter the moment.",
            "speaker": "SPEAKER_00",
            "words": [],
        },
        {
            "start": 11,
            "end": 14,
            "text": "Sam will show the evidence card after the question.",
            "speaker": "SPEAKER_01",
            "words": [],
        },
        {
            "start": 15,
            "end": 19,
            "text": "Use the fixture day if the live capture is unavailable.",
            "speaker": "SPEAKER_00",
            "words": [],
        },
    ]
    for index, segment in enumerate(segments):
        event(
            f"ev_quote_{index}",
            f"12:40:{segment['start']:02d}",
            "said.quote",
            "Demo planning quote",
            "cafe2",
            moment_id="mo_001",
            speaker=segment["speaker"],
            text=segment["text"],
            source={"kind": "fixture", "ref": f"transcript:mo_001:{index}"},
        )
    transits = [
        {"from": a["id"], "to": b["id"], "t0": a["t1"], "t1": b["t0"], "mode": "walk", "conf": 0.5}
        for a, b in zip(places, places[1:], strict=False)
    ]
    write(
        FIXTURE / "day.json",
        {
            "schema": "daycube.day/1",
            "date": "2026-10-20",
            "tz": "America/Toronto",
            "places": places,
            "transits": transits,
            "cube_faces": ["ev_001", "ev_004", "ev_007", "mo_001", "ev_quote_0", "ev_011"],
            "stats": {"events": len(events), "moments": 1, "places": 5, "floor_changes": 1},
            "summary": "Synthetic demonstration data. No personal activity was captured.",
        },
    )
    lines(FIXTURE / "events.jsonl", events)
    features = []
    for place in places:
        start, end = datetime.fromisoformat(place["t0"]), datetime.fromisoformat(place["t1"])
        for index in range(12):
            floor = 2 if place["id"] == "hall" and index > 0 else 0
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [place["lon"], place["lat"]]},
                    "properties": {
                        "t": (start + (end - start) * index / 12).isoformat(),
                        "alt_rel": floor * 3.2,
                        "floor": floor,
                        "floor_conf": 0.7,
                        "place": place["id"],
                        "x": round(-6 + index, 1),
                        "y": 0,
                        "pos_conf": 0.4,
                        "fidelity": "illustrative",
                        "source": "fixture",
                    },
                }
            )
    write(FIXTURE / "skeleton.geojson", {"type": "FeatureCollection", "features": features})
    records = []
    for shot, time, title, texts in [
        (
            "0910_00",
            "09:10:00",
            "DAYCUBE project notes",
            ["Fixture first", "The vault is the integration contract", "Known, estimated and illustrative"],
        ),
        (
            "1005_00",
            "10:05:00",
            "Pull request 12",
            ["Add hinge inference loop", "Review the API contracts before merging", "Build the shared fixture"],
        ),
    ]:
        records.append(
            {
                "shot": shot,
                "t": stamp(time),
                "app": "chrome.exe",
                "title": title,
                "w": 1920,
                "h": 1080,
                "blocks": [
                    {
                        "id": f"b{i}",
                        "bbox": [120, 210 + i * 100, 980, 262 + i * 100],
                        "text": text,
                        "conf": 0.93,
                        "attention": 0.71,
                        "dwell_ms": 4200,
                    }
                    for i, text in enumerate(texts)
                ],
            }
        )
        thumbnail = FIXTURE / f"screens/{shot}.svg"
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        thumbnail.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="270"><rect width="480" height="270" fill="#141c28"/><text x="28" y="70" fill="#ffb454" font-family="sans-serif" font-size="20">{title}</text><text x="28" y="110" fill="#b8c0cf" font-family="sans-serif" font-size="14">Illustrative fixture thumbnail</text></svg>',
            encoding="utf-8",
        )
    lines(FIXTURE / "screens/ocr.jsonl", records)
    write(
        FIXTURE / "moments/mo_001/meta.json",
        {
            "id": "mo_001",
            "title": "Demo planning",
            "t0": stamp("12:40:00"),
            "duration_s": 20,
            "place": "cafe2",
            "floor": 0,
            "lat": None,
            "lon": None,
            "camera": {"fov_deg": 62},
            "fidelity": "illustrative",
            "media_available": False,
            "consented_people": [],
        },
    )
    write(FIXTURE / "moments/mo_001/transcript.json", {"segments": segments, "speaker_map": {}})
    write(
        FIXTURE / "moments/mo_001/scene.json",
        {
            "kind": "unavailable",
            "reason": "Synthetic transcript fixture; no source media or reconstruction is supplied.",
        },
    )
    for folder in ("keyframes", "depth"):
        directory = FIXTURE / "moments/mo_001" / folder
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".gitkeep").touch()
    nodes = [
        {"id": "mo_001", "kind": "moment", "label": "Demo planning"},
        {"id": "person:sam", "kind": "person", "label": "Sam (fictional)"},
        {"id": "place:cafe2", "kind": "place", "label": "Maple Café"},
        {"id": "topic:demo", "kind": "topic", "label": "Demo"},
        {"id": "ev_007", "kind": "event", "label": "Read pull request"},
    ]
    write(
        FIXTURE / "graph.json",
        {
            "nodes": nodes,
            "edges": [
                {"from": "mo_001", "to": "topic:demo", "rel": "about", "evidence": ["transcript:mo_001:0"]},
                {"from": "mo_001", "to": "place:cafe2", "rel": "at", "evidence": ["ev_009"]},
            ],
        },
    )
    levels = [
        {
            "index": floor,
            "walls": [[[0, 0], [18, 0]], [[18, 0], [18, 11]]],
            "doors": [[[3, 0], [5, 0]]],
            "stairs": [[15, 4.5, 2.4, 2]],
            "rooms": [{"id": f"room-{floor}", "poly": [[0, 0], [18, 0], [18, 4], [0, 4]], "kind": "office"}],
            "graph": {"nodes": [[1, 2], [6, 2], [6, 8]], "edges": [[0, 1], [1, 2]]},
        }
        for floor in (0, 1, 2)
    ]
    write(
        ROOT / "venues/engineering-hall.json",
        {
            "schema": "daycube.venue/1",
            "id": "engineering-hall",
            "name": "Engineering Hall (fictional)",
            "entrance": {"lat": 43.662, "lon": -79.388},
            "axis_deg": 12,
            "floor_height_m": 3.2,
            "floors": 3,
            "levels": levels,
            "wifi": [],
        },
    )
    print("Rebuilt the fictional sample day: 5 visits, 60 fixes, 14 events, 1 transcript-only moment")


if __name__ == "__main__":
    main()

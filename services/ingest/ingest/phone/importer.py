"""A phone's recordings of a day into the vault: visits, trips, floors, the path, and moments.

``import_phone`` takes whatever was exported from the phone, in any mix:

- Sensor Logger recordings (zip or folder): visits and trips (``visits``), floors from the
  barometer (``skeleton.floors``) and every GPS fix as the day's skeleton;
- voice memos (.m4a, .wav, ...): audio moments with their transcript (``voice``);
- Stray Scanner captures (folder, or rgb.mp4 + odometry.csv): video moments with keyframes and
  the camera's path on the map (``stray``).

Each local date touched becomes or updates that vault day. Everything imported carries ids of its
own (places ``ph-HHMM``, events ``ph_...``, moments ``mo_voice_...``/``mo_scan_...``) and its source
on every skeleton point, so importing again replaces exactly what an earlier import wrote, and
records from anywhere else (computer capture, other imports) are kept. Moments are given the visit
they overlap most.

What the phone does not measure is not invented: positions inside a building are not known (GPS
indoors wanders tens of metres), so fixes in a house or building visit carry no stage position, and
an outdoor visit's positions are metres east and north of its middle.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .. import contracts
from ..moments.media import executable
from ..skeleton.floors import estimate_floors, floor_events
from ..vault import Record, Vault, read_json, read_lines
from . import sensor_logger, stray, voice
from .geo import local
from .visits import Span, segment

PLACE_PREFIX, EVENT_PREFIX = "ph-", "ph_"
MOMENT_PREFIXES = ("mo_voice_", "mo_scan_")
SOURCES = frozenset({sensor_logger.SOURCE})
KIND_NAMES = {"house": "Home", "venue": "Building", "outdoor": "Outdoors"}


@dataclass
class Moment:
    id: str
    kind: str  # "audio" or "video"
    t0: datetime
    duration_s: float
    title: str
    fov_deg: float
    write: Any  # callable(folder) -> dict scene, writes the media
    transcript: dict = field(default_factory=lambda: {"segments": [], "speaker_map": {}})
    lat: float | None = None
    lon: float | None = None
    source: dict = field(default_factory=dict)
    place: str | None = None  # the visit it overlaps most
    file: dict = field(default_factory=dict)  # the file it came from: name and size


def classify(paths: list[Path]) -> dict[str, list]:
    """Which of ``paths`` are Sensor Logger recordings, voice memos, Stray Scanner captures and
    transcripts (``<audio stem>.json`` beside a memo, or any .json given with one memo)."""
    sensors, audio, transcripts, rest = [], [], [], []
    for path in paths:
        if sensor_logger.is_recording(path):
            sensors.append(path)
        elif path.is_file() and path.suffix.lower() in voice.AUDIO_SUFFIXES:
            audio.append(path)
        elif path.is_file() and path.suffix.lower() == ".json":
            transcripts.append(path)
        else:
            rest.append(path)
    return {"sensor": sensors, "audio": audio, "transcripts": transcripts, "scans": stray.find(rest)}


def import_phone(
    vault: Vault,
    paths: list[Path],
    *,
    transcribe: bool = True,
    audio_ends_at_creation: bool = True,
    say: Any = print,
) -> dict:
    """Import ``paths`` (see the module). Returns what was written per day (``days``) and what was
    left out and why (``skipped``: a memo or scan that could not be read, say with FFmpeg missing);
    the rest is imported regardless. Raises ValueError when nothing could be."""
    found = classify([Path(p) for p in paths])
    if not any(found[k] for k in ("sensor", "audio", "scans")):
        raise ValueError("Nothing to import: no Sensor Logger recording, voice memo or Stray Scanner capture")
    recordings = [sensor_logger.parse(p) for p in found["sensor"]]
    zone_name = recordings[0].tz if recordings else vault_timezone(vault)
    zone = ZoneInfo(zone_name)
    fixes = sorted((f for r in recordings for f in r.fixes), key=lambda f: f.t)
    spans = [(r, s) for r in recordings for s in segment(r)]
    moments: list[Moment] = []
    skipped: list[dict] = []
    for path in found["audio"]:
        try:
            moments.append(_voice(path, found["transcripts"], len(found["audio"]) == 1, transcribe,
                                  audio_ends_at_creation, say))  # fmt: skip
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            skipped.append({"paths": [str(path)], "reason": str(error)})
    for files in found["scans"]:
        try:
            moments.append(_scan(files, fixes, say))
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            skipped.append({"paths": [str(f) for f in files.values()], "reason": str(error)})
    for item in skipped:
        say(f"left out {', '.join(Path(p).name for p in item['paths'])}: {item['reason']}")
    if not spans and not moments:
        raise ValueError("Nothing could be imported: " + "; ".join(item["reason"] for item in skipped))
    days: dict[str, dict] = defaultdict(lambda: {"spans": [], "moments": []})
    for recording, span in spans:
        for day, piece in _by_day(span, zone):
            days[day]["spans"].append((recording, piece))
    for moment in moments:
        days[moment.t0.astimezone(zone).date().isoformat()]["moments"].append(moment)
    written = {
        day: _write_day(vault, day, zone_name, parts["spans"], parts["moments"], say)
        for day, parts in sorted(days.items())
    }
    return {"days": written, "skipped": skipped}


def vault_timezone(vault: Vault) -> str:
    from ..config import Settings

    return Settings().timezone


def _voice(path: Path, transcripts: list[Path], only: bool, run: bool, ends: bool, say: Any) -> Moment:
    audio = voice.read(path, ends_at_creation=ends)
    beside = [t for t in transcripts if t.stem == path.stem] or (transcripts if only and len(transcripts) == 1 else [])
    transcript = (
        voice.load_transcript(beside[0], audio.duration_s) if beside else (voice.transcribe(path, say) if run else None)
    )
    # Named by the file's own time, so a later import that reads it differently replaces it.
    moment_id = "mo_voice_" + (audio.stamp or audio.t0).strftime("%Y%m%d_%H%M%S")
    note = (
        None if transcript is not None else "not transcribed: install the transcribe extra (uv sync --extra transcribe)"
    )

    def write(folder: Path) -> dict:
        name = voice.store(audio, folder)
        scene = {"kind": "audio", "audio": name, "stamped": audio.stamped}
        if note:
            scene["transcript"] = note
        return scene

    title = path.stem.split("-", 1)[-1].replace("_", " ") if "-" in path.stem else path.stem.replace("_", " ")
    return Moment(moment_id, "audio", audio.t0, audio.duration_s, f"Voice memo · {title}", 60.0, write,
                  transcript or {"segments": [], "speaker_map": {}},
                  source={"kind": "voice-memo", "ref": f"voice-memo:{path.name}"},
                  file={"name": path.name, "bytes": path.stat().st_size})  # fmt: skip


def _scan(files: dict[str, Path], fixes: list, say: Any) -> Moment:
    executable("ffmpeg")  # for the keyframes, before anything is written
    scan = stray.read(files)
    fit = stray.georeference(scan, fixes) if fixes else None
    if fit:
        say(f"scan placed on the map: {fit['rmse_m']} m from the GPS track, start {fit['offset_s']:+d} s")
    moment_id = "mo_scan_" + (scan.stamp or scan.t0).strftime("%Y%m%d_%H%M%S")

    def write(folder: Path) -> dict:
        name = stray.store(scan, folder)
        kept = stray.keep_capture(files, folder / "capture")  # depth and poses, for 3D (ingest/splat)
        frames = stray.keyframes(scan, folder)
        watch = stray.preview(scan, folder)
        (folder / "camera.json").write_text(json.dumps({"fit": scan.fit, "points": stray.camera_path(scan)}) + "\n",
                                            encoding="utf-8")  # fmt: skip
        return {
            "kind": "camera-path", "video": name, "preview": watch, "frames": len(scan.t),
            "size": [scan.width, scan.height],
            "rotate_deg": stray.upright(scan),  # clockwise to stand upright (the preview and keyframes are)
            "keyframes": frames, "path": "camera.json", "georeferenced": scan.fit, "stamped": scan.stamped,
            "depth": None if scan.depth is None else "provided, not yet fused",
            "capture": kept,
            "reason": None if scan.depth is not None else
            "A 3D scene needs the capture's depth folder (one PNG per frame); only the video and camera path came.",
        }  # fmt: skip

    lat = float(scan.geo[0, 0]) if scan.geo is not None else None
    lon = float(scan.geo[0, 1]) if scan.geo is not None else None
    return Moment(moment_id, "video", scan.t0, scan.duration_s, "LiDAR scan", scan.fov_deg, write, lat=lat, lon=lon,
                  source={"kind": "stray-scanner", "ref": f"stray-scanner:{scan.video.name}"},
                  file={"name": scan.video.name, "bytes": scan.video.stat().st_size})  # fmt: skip


def _by_day(span: Span, zone: ZoneInfo) -> list[tuple[str, Span]]:
    """The span cut at local midnights."""
    out = []
    t0 = span.t0
    while t0 < span.t1:
        local_start = datetime.fromtimestamp(t0, zone)
        midnight = datetime.combine(local_start.date() + timedelta(days=1), time(), zone).timestamp()
        t1 = min(span.t1, midnight)
        piece = Span(**{**span.__dict__, "t0": t0, "t1": t1})
        piece.fixes = [f for f in span.fixes if t0 <= f.t.timestamp() <= t1]
        out.append((local_start.date().isoformat(), piece))
        t0 = t1
    return out


def _iso(t: float, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(t, zone).isoformat(timespec="seconds")


def _write_day(vault: Vault, day: str, zone_name: str, spans: list, moments: list[Moment], say: Any) -> dict:
    zone = ZoneInfo(zone_name)
    path = vault.day_path(day, must_exist=False)
    existing = read_json(path / "day.json") if (path / "day.json").is_file() else None
    if existing is not None and existing["tz"] != zone_name:
        raise ValueError(f"{day} is kept in {existing['tz']}; the phone recorded in {zone_name}")
    replace_sensor = bool(spans)
    keep_places = [
        p for p in (existing or {}).get("places", []) if not (replace_sensor and p["id"].startswith(PLACE_PREFIX))
    ]
    keep_transits = [t for t in (existing or {}).get("transits", [])
                     if not (replace_sensor and t["from"].startswith(PLACE_PREFIX))]  # fmt: skip
    events = read_lines(path / "events.jsonl") if existing is not None else []
    skeleton = (
        read_json(path / "skeleton.geojson")
        if (path / "skeleton.geojson").is_file()
        else {"type": "FeatureCollection", "features": []}
    )
    # A moment an earlier import made from the same file under another id (read differently then)
    # is replaced too: its scene names the file it came from.
    new_moment_ids = {m.id for m in moments} | _same_files(path, moments)
    for stale in new_moment_ids - {m.id for m in moments}:
        shutil.rmtree(path / "moments" / stale, ignore_errors=True)
    events = [e for e in events if not (
        (replace_sensor and e["id"].startswith(EVENT_PREFIX) and e["source"]["kind"] in SOURCES | {"barometer"})
        or e.get("moment_id") in new_moment_ids
    )]  # fmt: skip
    if replace_sensor:
        skeleton["features"] = [f for f in skeleton["features"] if f["properties"]["source"] not in SOURCES]

    places, transits, new_events, features = _sensor_records(day, spans, zone)
    all_places = keep_places + places
    for moment in moments:
        moment.place = _overlap(moment, all_places)
    all_places.sort(key=lambda p: p["t0"])
    for index, place in enumerate(all_places):
        place["layer_index"] = index
    ids = {p["id"] for p in all_places}
    transits = [t for t in keep_transits + transits if t["from"] in ids and t["to"] in ids]
    moment_events = []
    for moment in moments:
        folder = path / "moments" / moment.id
        # A scan's 3D (ingest/splat) took the GPU minutes to make: it survives importing the same
        # capture again.
        kept_splat = None
        if folder.exists():
            before = read_json(folder / "scene.json") if (folder / "scene.json").is_file() else {}
            if (folder / "splat").is_dir() and before.get("source") == moment.file and before.get("splat"):
                kept_splat = (path / "moments" / f".{moment.id}.splat", before["splat"])
                if kept_splat[0].exists():
                    shutil.rmtree(kept_splat[0])
                (folder / "splat").rename(kept_splat[0])
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        scene = moment.write(folder) | {"source": moment.file}
        if kept_splat:
            kept_splat[0].rename(folder / "splat")
            scene["splat"] = kept_splat[1]
        meta = {
            "id": moment.id, "title": moment.title, "t0": moment.t0.astimezone(zone).isoformat(timespec="seconds"),
            "duration_s": round(moment.duration_s, 3), "lat": moment.lat, "lon": moment.lon,
            "place": moment.place, "floor": None, "camera": {"fov_deg": moment.fov_deg}, "fidelity": "known",
            "media_available": True, "consented_people": [],
        }  # fmt: skip
        contracts.validate("moment", meta)
        contracts.validate("transcript", moment.transcript)
        _json(folder / "meta.json", meta)
        _json(folder / "scene.json", scene)
        _json(folder / "transcript.json", moment.transcript)
        said = len(moment.transcript["segments"])
        minutes = moment.duration_s / 60
        moment_events.append({
            "id": f"{EVENT_PREFIX}{moment.id}", "t": meta["t0"],
            "t_end": (moment.t0 + timedelta(seconds=moment.duration_s)).astimezone(zone).isoformat(timespec="seconds"),
            "type": f"moment.{moment.kind}", "label": moment.title, "place": moment.place, "floor": None,
            "x": None, "y": None, "position_fidelity": "estimated", "source": moment.source, "confidence": 0.9,
            "fidelity": "known", "attention": None, "people": [], "topics": [],
            "text": f"{minutes:.1f} min" + (f", {said} transcribed segments" if said else ""),
            "moment_id": moment.id,
        })  # fmt: skip
    events = sorted(events + new_events + moment_events, key=lambda e: (e["t"], e["id"]))
    features = sorted(skeleton["features"] + features, key=lambda f: f["properties"]["t"])
    skeleton = {"type": "FeatureCollection", "features": features}
    contracts.validate_all("event", events)
    contracts.validate("skeleton", skeleton)
    data = {
        "schema": "daycube.day/1", "date": day, "tz": zone_name, "places": all_places, "transits": transits,
        "cube_faces": [f for f in (existing or {}).get("cube_faces", []) if f in {e["id"] for e in events}],
        "stats": {},
    }  # fmt: skip
    if existing and existing.get("summary"):
        data["summary"] = existing["summary"]
    moment_count = (
        len({p.parent.name for p in (path / "moments").glob("*/meta.json")}) if (path / "moments").exists() else 0
    )
    data["stats"] = vault._stats(data, events, moment_count)
    contracts.validate("day", data)
    with vault.lock:
        vault.write_lines(path / "events.jsonl", events)
        vault.write_json(path / "skeleton.geojson", skeleton)
        if not (path / "graph.json").is_file():
            vault.write_json(path / "graph.json", {"nodes": [], "edges": []})
        vault.write_json(path / "day.json", data)
    vault.invalidate_index(day)
    vault.validate(day)
    result = {"places": len(places), "transits": len(transits), "fixes": len(features), "moments": [m.id for m in moments],
              "events": len(new_events) + len(moment_events)}  # fmt: skip
    say(f"{day}: {result['places']} visits, {len(transits)} trips, {len(new_events)} place events, "
        f"{len(moments)} moments")  # fmt: skip
    return result


def _same_files(day: Path, moments: list[Moment]) -> set[str]:
    """Ids of moments already in the day that came from the files being imported."""
    files = {(m.file.get("name"), m.file.get("bytes")) for m in moments}
    out = set()
    for scene_path in (day / "moments").glob("mo_*/scene.json") if (day / "moments").exists() else []:
        if not scene_path.parent.name.startswith(MOMENT_PREFIXES):
            continue
        try:
            source = read_json(scene_path).get("source") or {}
        except (OSError, ValueError):
            continue
        if (source.get("name"), source.get("bytes")) in files:
            out.add(scene_path.parent.name)
    return out


def _overlap(moment: Moment, places: list[Record]) -> str | None:
    a, b = moment.t0.timestamp(), moment.t0.timestamp() + moment.duration_s
    best, most = None, 0.0
    for place in places:
        shared = min(b, datetime.fromisoformat(place["t1"]).timestamp()) - max(
            a, datetime.fromisoformat(place["t0"]).timestamp()
        )
        if shared > most:
            best, most = place["id"], shared
    return best


def _sensor_records(day: str, spans: list, zone: ZoneInfo) -> tuple[list, list, list, list]:
    places: list[Record] = []
    transits: list[Record] = []
    events: list[Record] = []
    features: list[Record] = []
    stays = [(r, s) for r, s in spans if s.kind == "stay" and s.t1 - s.t0 >= 30]
    names = _names(stays)
    ids: dict[int, str] = {}
    for (recording, stay), name in zip(stays, names, strict=True):
        pid = PLACE_PREFIX + datetime.fromtimestamp(stay.t0, zone).strftime("%H%M%S")
        ids[id(stay)] = pid
        source = {"kind": sensor_logger.SOURCE, "ref": f"{sensor_logger.SOURCE}:{recording.name or 'recording'}"}
        floors, track, times = [0], None, []
        if stay.mode != "outdoor":
            samples = [(t, a) for t, a in recording.altitude if stay.t0 <= t <= stay.t1]
            if len(samples) >= 10:
                times = [t for t, _ in samples]
                track = estimate_floors(times, [a for _, a in samples], height_known=False)
                floors = sorted(set(track.floors))
        place = {
            "id": pid, "name": name, "kind": stay.mode, "t0": _iso(stay.t0, zone), "t1": _iso(stay.t1, zone),
            "lat": None if stay.lat is None else round(stay.lat, 6), "lon": None if stay.lon is None else round(stay.lon, 6),
            "osm_id": None, "venue": None, "floors_visited": floors, "conf": stay.conf, "layer_index": 0,
        }  # fmt: skip
        places.append(place)
        km = stay.distance_m / 1000
        label = f"Walked {km:.1f} km outdoors" if stay.mode == "outdoor" else f"At {name}"
        detail = [f"{(stay.t1 - stay.t0) / 60:.0f} min"]
        if stay.steps:
            detail.append(f"{stay.steps:,} steps")
        events.append({
            "id": f"{EVENT_PREFIX}visit_{pid[3:]}", "t": place["t0"], "t_end": place["t1"], "type": "place.visit",
            "label": label, "place": pid, "floor": floors[0], "x": None, "y": None, "position_fidelity": "estimated",
            "source": source, "confidence": stay.conf, "fidelity": "estimated", "attention": None, "people": [],
            "topics": [], "text": ", ".join(detail), "conf": stay.conf,
        })  # fmt: skip
        if track is not None:
            for event in floor_events(track, place=pid, iso=lambda s: _iso(s, zone), source_ref=source["ref"]):
                event["id"] = f"{EVENT_PREFIX}floor_{pid[3:]}_{event['id'].rsplit('_', 1)[-1]}"
                events.append(event)
        for fix in stay.fixes:
            t = fix.t.timestamp()
            floor, floor_conf = floors[0], None
            if track is not None and times:
                k = min(range(len(times)), key=lambda i: abs(times[i] - t)) if len(times) < 64 else _nearest(times, t)
                floor, floor_conf = track.floors[k], track.confidence[k]
            x = y = None
            if stay.mode == "outdoor" and stay.lat is not None:
                x, y = (round(v, 2) for v in local(fix.lat, fix.lon, stay.lat, stay.lon))
            features.append(_feature(fix, pid, floor, floor_conf, x, y))
    stay_ids = [ids[id(s)] for _, s in stays]
    by_time = sorted(zip([s for _, s in stays], stay_ids, strict=True), key=lambda p: p[0].t0)
    for recording, trip in spans:
        if trip.kind != "trip":
            continue
        before = [pid for s, pid in by_time if s.t1 <= trip.t0 + 1]
        after = [pid for s, pid in by_time if s.t0 >= trip.t1 - 1]
        for fix in trip.fixes:
            features.append(_feature(fix, None, None, None, None, None))
        if not before or not after:
            continue
        mode = trip.mode if trip.mode in ("walk", "vehicle", "bike") else "unknown"
        transit = {"from": before[-1], "to": after[0], "t0": _iso(trip.t0, zone), "t1": _iso(trip.t1, zone),
                   "mode": mode, "conf": trip.conf}  # fmt: skip
        transits.append(transit)
        verb = {"vehicle": "Drove", "walk": "Walked", "bike": "Cycled"}.get(mode, "Travelled")
        events.append({
            "id": f"{EVENT_PREFIX}transit_{datetime.fromtimestamp(trip.t0, zone).strftime('%H%M%S')}",
            "t": transit["t0"], "t_end": transit["t1"], "type": "place.transit",
            "label": f"{verb} {trip.distance_m / 1000:.1f} km", "place": None, "floor": None, "x": None, "y": None,
            "position_fidelity": "estimated",
            "source": {"kind": sensor_logger.SOURCE, "ref": f"{sensor_logger.SOURCE}:{recording.name or 'recording'}"},
            "confidence": trip.conf, "fidelity": "estimated", "attention": None, "people": [], "topics": [],
            "text": f"{(trip.t1 - trip.t0) / 60:.0f} min", "from": transit["from"], "to": transit["to"], "mode": mode,
        })  # fmt: skip
    return places, transits, events, features


def _nearest(times: list[float], t: float) -> int:
    from bisect import bisect_left

    i = bisect_left(times, t)
    if i == 0:
        return 0
    if i >= len(times):
        return len(times) - 1
    return i if times[i] - t < t - times[i - 1] else i - 1


def _names(stays: list) -> list[str]:
    """A name per stay: the recording's (the app names it after where it began) for stays where it
    began; otherwise by kind and time."""
    from .geo import metres

    out = []
    for recording, stay in stays:
        start = recording.fixes[0] if recording.fixes else None
        here = (
            start is not None and stay.lat is not None and recording.name
            and any(c.isalpha() for c in recording.name) and metres(stay.lat, stay.lon, start.lat, start.lon) <= 150
        )  # fmt: skip
        if here:
            out.append(recording.name)
        else:
            out.append(KIND_NAMES.get(stay.mode, "Place"))
    return out


def _feature(
    fix: Any, place: str | None, floor: int | None, floor_conf: float | None, x: float | None, y: float | None
) -> Record:
    accuracy = fix.hacc if fix.hacc is not None else 30.0
    properties: Record = {
        "t": fix.t.isoformat(timespec="milliseconds"), "alt_rel": None if fix.alt_rel is None else round(fix.alt_rel, 2),
        "floor": floor, "place": place, "x": x, "y": y,
        "pos_conf": round(min(0.95, max(0.05, 1 - accuracy / 50)), 3), "fidelity": "estimated", "source": fix.source,
    }  # fmt: skip
    if floor_conf is not None:
        properties["floor_conf"] = round(min(1.0, max(0.0, floor_conf)), 3)
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(fix.lon, 7), round(fix.lat, 7)]},
            "properties": properties}  # fmt: skip


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

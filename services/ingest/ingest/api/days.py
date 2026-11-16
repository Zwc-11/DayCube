"""A day's records: listing days, reading one (its events, moments and files) and correcting it."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import Field

from ..capture.synthesize import capture_days
from ..generated.event import Event
from ..vault import Record, read_json
from .deps import AUTHORIZED, RequestModel, TheSettings, TheStore, TheVault, conditional

router = APIRouter()

MEDIA_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".svg", ".mp4", ".wav", ".m4a", ".mp3", ".glb", ".ply", ".spz", ".splat"}
)
# Types the platform may not know (Windows has no .m4a in its registry by default).
MEDIA_TYPES = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".glb": "model/gltf-binary",
    ".spz": "application/octet-stream",
    ".splat": "application/octet-stream",
}
SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
SAFE_FILE = re.compile(r"[A-Za-z0-9_-]+\.json")


class EventsRequest(RequestModel):
    date: str
    events: list[Event] = Field(min_length=1, max_length=1000)


class RenameRequest(RequestModel):
    place_id: str
    name: str = Field(min_length=1, max_length=120)


class SpeakerRequest(RequestModel):
    date: str
    moment_id: str
    diar_label: str
    person_id: str = Field(pattern=r"^person:[\w-]{1,80}$")  # letters of any script: names are not only ASCII


@router.get("/health")
def health(settings: TheSettings, store: TheStore) -> Record:
    return {
        "status": "ok",
        "version": "0.1.0",
        "timezone": settings.timezone,
        "capabilities": {
            "vault": True,
            "fts_search": True,
            "ask_mode": "extractive",
            "vector_search": store.vectors,
            "capture": False,
            "gpu_pipeline": False,
            "delete_hours": True,
            "world": True,
        },
    }


@router.get("/days")
def days(vault: TheVault, store: TheStore) -> list[Record]:
    """Newest first: vault days, and days with only computer capture (no places). ``capture``
    says the day has capture logs; ``records`` is its searchable record count once indexed."""
    listed = {d["date"]: {**d, "capture": False, "records": None} for d in vault.list_days()}
    for day in capture_days(vault):
        listed.setdefault(
            day,
            {
                "date": day,
                "stats": {"events": 0, "moments": 0, "places": 0, "floor_changes": 0},
                "kinds": [],
                "records": None,
            },
        )["capture"] = True
    if store.path.exists():
        with store.connect() as db:
            for day, units in db.execute("SELECT day, units FROM days"):
                if day in listed:
                    listed[day]["records"] = units
    return [listed[day] for day in sorted(listed, reverse=True)]


@router.get("/day/{date}")
def day(date: str, request: Request, vault: TheVault) -> Response:
    """Serialized once per change of the day's files; repeat loads replay the same bytes, and a
    client holding the current copy gets an empty 304."""
    return conditional(request, *vault.tagged_day(date))


@router.get("/day/{date}/events")
def events(
    date: str, vault: TheVault, t0: str | None = None, t1: str | None = None, type: str | None = None
) -> list[Record]:
    return vault.events_in(date, t0, t1, type)


@router.get("/day/{date}/skeleton")
def skeleton(date: str, vault: TheVault) -> Record:
    return vault.snapshot(date).skeleton


@router.get("/day/{date}/graph")
def graph(date: str, vault: TheVault) -> Record:
    return vault.snapshot(date).graph


@router.get("/day/{date}/seen")
def day_seen(date: str, vault: TheVault, store: TheStore) -> Record:
    """What was on screen through the day, page by page (ingest/seen.py)."""
    from ..seen import seen

    vault.day_path(date, must_exist=False)
    return seen(store, date)


@router.get("/moment/{moment_id}")
def moment(moment_id: str, vault: TheVault, date: str = Query(...)) -> Record:
    if not SAFE_ID.fullmatch(moment_id):
        raise ValueError("Invalid moment id")
    path = vault.day_path(date) / "moments" / moment_id
    scene = read_json(path / "scene.json")
    out = {"meta": read_json(path / "meta.json"), "scene": scene, "transcript": read_json(path / "transcript.json")}
    # A capture's camera path (phone imports): small enough to send with the moment.
    camera = scene.get("path") if isinstance(scene, dict) else None
    if isinstance(camera, str) and SAFE_FILE.fullmatch(camera) and (path / camera).is_file():
        out["camera"] = read_json(path / camera)
    # A scan in 3D: where the camera was every few frames, for the viewer to follow the walk.
    splat = scene.get("splat") if isinstance(scene, dict) else None
    if isinstance(splat, dict) and (path / "splat" / "cameras.json").is_file():
        out["splat_cameras"] = read_json(path / "splat" / "cameras.json")
    # A recording's speech (services/speech): per turn its emotion and fillers, sound events,
    # and whose voice each speaker is.
    if (path / "speech.json").is_file():
        speech = read_json(path / "speech.json")
        out["speech"] = {k: speech.get(k) for k in ("languages", "speakers", "turns", "events", "voices", "models")}
    return out


@router.get("/vault/{date}/{asset_path:path}")
def asset(date: str, asset_path: str, vault: TheVault) -> FileResponse:
    day_path = vault.day_path(date)
    candidate = (day_path / asset_path).resolve()
    if (
        not candidate.is_relative_to(day_path)
        or candidate.suffix.lower() not in MEDIA_SUFFIXES
        or not candidate.is_file()
    ):
        raise HTTPException(404, "Asset not found")
    return FileResponse(
        candidate,
        media_type=MEDIA_TYPES.get(candidate.suffix.lower()),
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"},
    )


@router.post("/events", dependencies=AUTHORIZED)
def add_events(body: EventsRequest, vault: TheVault) -> Record:
    accepted = vault.append_events(
        body.date, [event.model_dump(mode="json", by_alias=True, exclude_unset=True) for event in body.events]
    )
    return {"accepted": accepted}


@router.post("/places/rename", dependencies=AUTHORIZED)
def rename(body: RenameRequest, vault: TheVault) -> Record:
    if not body.name.strip():
        raise ValueError("Name must not be blank")
    if not vault.has_place(body.place_id):
        raise HTTPException(404, "Place not found")
    with vault.lock:
        names = read_json(vault.root / "places.json")
        names[body.place_id] = body.name.strip()
        vault.write_json(vault.root / "places.json", names)
    return {"ok": True}


@router.post("/speakers/confirm", dependencies=AUTHORIZED)
def confirm(body: SpeakerRequest, vault: TheVault) -> Record:
    if not SAFE_ID.fullmatch(body.moment_id):
        raise ValueError("Invalid moment id")
    with vault.lock:
        path = vault.day_path(body.date) / "moments" / body.moment_id / "transcript.json"
        transcript = read_json(path)
        if body.diar_label not in {s["speaker"] for s in transcript["segments"]}:
            raise ValueError("Speaker label is not present in the transcript")
        transcript["speaker_map"][body.diar_label] = body.person_id
        vault.write_json(path, transcript)
        vault.invalidate_index(body.date)
        # Given by hand, even when it is the name the voice already had.
        speech_path = path.with_name("speech.json")
        speech = read_json(speech_path) if speech_path.is_file() else None
        if isinstance(speech, dict) and isinstance(speech.get("voices"), dict):
            entry = speech["voices"].setdefault(body.diar_label, {"voice": None})
            entry.update(person=body.person_id, how="confirmed")
            vault.write_json(speech_path, speech)
    # The name goes with the voice: every other recording where it speaks gets it too.
    from ..voices import update as spread_names

    spread_names(vault)
    return {"ok": True, "message": "Speaker named here and wherever their voice speaks"}

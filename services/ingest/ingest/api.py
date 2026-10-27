from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from datetime import date as date_type
from datetime import time as time_type
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .ask.retrieve import retrieve
from .ask.service import ask
from .capture.synthesize import capture_days
from .config import Settings
from .contracts import ContractError
from .generated.event import Event
from .index.embed import Embedder, default_embedder
from .notes.graph import NoteGraph
from .notes.read import DayNotes, attach_notes, read_note
from .store import EmbeddingModelChanged, LifeStore
from .time import instant
from .vault import Record, Vault, read_json

log = logging.getLogger("daycube.api")

MEMORY_FILES = {"photo.jpg": "image/jpeg", "memory.splat": "application/octet-stream"}
MEDIA_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".svg", ".mp4", ".wav", ".m4a", ".mp3", ".glb", ".ply", ".spz", ".splat"})
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
MAX_JOBS = 100


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AskRequest(RequestModel):
    date: str | None = None  # None: search every day in the vault
    question: str = Field(min_length=1, max_length=2000)


class EventsRequest(RequestModel):
    date: str
    events: list[Event] = Field(min_length=1, max_length=1000)


class IngestRequest(RequestModel):
    steps: list[str] = Field(default_factory=lambda: ["index"], min_length=1)


class RenameRequest(RequestModel):
    place_id: str
    name: str = Field(min_length=1, max_length=120)


class SpeakerRequest(RequestModel):
    date: str
    moment_id: str
    diar_label: str
    person_id: str = Field(pattern=r"^person:[\w-]{1,80}$")  # letters of any script: names are not only ASCII


def sse(event: str, value: Record) -> str:
    return f"event: {event}\ndata: {json.dumps(value)}\n\n"


def create_app(settings: Settings | None = None, *, embedder: Embedder | None = None, warm: bool = False) -> FastAPI:
    """``embedder`` replaces the configured model (tests pass a deterministic one). ``warm``: at
    startup, bring the vault database up to date and load the embedding model and vector index in
    the background, so the first question is answered as fast as the rest."""
    settings = settings or Settings()
    vault = Vault(settings.vault_root)
    store = LifeStore(
        vault, embedder or default_embedder(settings.vector_search, settings.embedding_model), zone=settings.timezone
    )
    jobs: dict[str, Record] = {}
    jobs_lock = threading.Lock()
    bearer = HTTPBearer(auto_error=False)

    day_notes = DayNotes(store)
    note_graph = NoteGraph(store, day_notes)

    def warm_up() -> None:
        started = perf_counter()
        try:
            store.ensure()
            retrieve(store, "warm up the search")
        except Exception:  # noqa: BLE001 - a cold first question is the only cost
            log.exception("Warming up search failed")
        else:
            log.info("Search warm in %.1f s", perf_counter() - started)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        vault.initialize()
        if warm:
            threading.Thread(target=warm_up, name="warm-up", daemon=True).start()
        yield

    app = FastAPI(
        title="DAYCUBE Local API",
        version="0.1.0",
        lifespan=lifespan,
        description="Local vault and extractive search. Synthetic fixture data is labeled illustrative.",
    )
    app.state.vault = vault
    app.state.store = store
    origins = settings.cors_origins.copy()
    if settings.extension_id:
        origins.append(f"chrome-extension://{settings.extension_id}")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if credentials is None or not secrets.compare_digest(credentials.credentials, vault.token):
            raise HTTPException(401, "A valid vault bearer token is required", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(FileNotFoundError)
    async def missing(request: Request, error: FileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "The requested vault record was not found"})

    @app.exception_handler(ValueError)
    async def invalid(request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.exception_handler(EmbeddingModelChanged)
    async def model_changed(request: Request, error: EmbeddingModelChanged) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(error)})

    @app.exception_handler(ContractError)
    async def contract_error(request: Request, error: ContractError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": error.message, "path": error.path})

    @app.get("/health")
    def health() -> Record:
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
                "delete_hours": False,
            },
        }

    @app.get("/days")
    def days() -> list[Record]:
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

    @app.get("/day/{date}")
    def day(date: str, request: Request) -> Response:
        # Serialized once per change of the day's files; repeat loads replay the same bytes, and
        # a client holding the current copy gets an empty 304 instead.
        body, etag = vault.tagged_day(date)
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if etag in (tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")):
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)

    @app.get("/day/{date}/events")
    def events(date: str, t0: str | None = None, t1: str | None = None, type: str | None = None) -> list[Record]:
        return vault.events_in(date, t0, t1, type)

    @app.get("/day/{date}/skeleton")
    def skeleton(date: str) -> Record:
        return vault.snapshot(date).skeleton

    @app.get("/day/{date}/graph")
    def graph(date: str) -> Record:
        return vault.snapshot(date).graph

    def epoch(value: str | None) -> float | None:
        """A date (local midnight, in the vault's time zone) or an ISO time, as epoch seconds."""
        if not value:
            return None
        if len(value) == 10:
            return datetime.combine(
                date_type.fromisoformat(value), time_type(), ZoneInfo(settings.timezone)
            ).timestamp()
        return instant(value).timestamp()

    @app.get("/graph")
    def knowledge_graph(
        since: str | None = None,
        until: str | None = None,
        kinds: str | None = None,
        limit: int = Query(200, ge=1, le=2000),
    ) -> Record:
        """Entities the records name (sites, pages, searches, courses, deadlines, places, people)
        most mentioned between ``since`` and ``until``, and the links among them."""
        return store.graph(
            since=epoch(since), until=epoch(until), kinds=kinds.split(",") if kinds else None, limit=limit
        )

    @app.get("/entity/{entity_id}")
    def entity(entity_id: int, limit: int = Query(50, ge=1, le=500)) -> Record:
        found = store.entity(entity_id, limit=limit)
        if found is None:
            raise FileNotFoundError(entity_id)
        return found

    @app.get("/search")
    def search(q: str = Query(..., min_length=1, max_length=500), date: str | None = None) -> Record:
        """Ask without the vault token, for search as you type: the same extractive answer and
        evidence as ``POST /ask``, each result with the note it is in (``note``) and the words to
        find there (``match``), and how long it took (``took_ms``). Reads only, like the other
        GET routes."""
        started = perf_counter()
        result = ask(store, date, q.strip())
        attach_notes(store, result["evidence"])
        result["took_ms"] = round((perf_counter() - started) * 1000, 1)
        return result

    @app.get("/day/{date}/notes")
    def knowledge(date: str) -> Record:
        """The day's knowledge base: its daily note (written or refreshed now if its records changed),
        the notes it links to and that link to it, and its local graph."""
        return day_notes.view(date)

    @app.get("/notes/graph")
    def notes_graph(request: Request) -> Response:
        """Every note in vault/notes (path, kind) and every [[link]] between them, links to notes
        not written yet as their own nodes (``missing``): the vault's graph. Every recorded day gets
        its daily note first. Tagged: a client holding the current copy gets an empty 304."""
        body, etag = note_graph.tagged()
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if etag in (tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")):
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)

    @app.get("/note")
    def note(path: str = Query(..., max_length=1000)) -> Record:
        """One note in vault/notes: its Markdown, its links (each with the note it opens) and backlinks."""
        return read_note(store, path)

    @app.get("/data")
    def data_center() -> Record:
        """Everything the vault holds: its source files by stream (days, capture logs, notes,
        calibration) and the database derived from them (rows per table, kinds, sizes)."""
        from .datacenter import report

        return report(store)

    @app.get("/gaze/learning")
    def gaze_learning(request: Request) -> Response:
        """The eye tracker's learning from clicks for the current calibration: every click's live
        error (the model in use and the calibration alone, on clicks neither had learned from), each
        fit with its held-out errors, and when each update reached the tracker's frames. Tagged: a
        client holding the current copy gets an empty 304."""
        from .gaze_learning import tagged

        body, etag = tagged(store.vault.root)
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if etag in (tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")):
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)

    @app.post("/notes/{date}", dependencies=[Depends(authorize)])
    def write_notes(date: str) -> Record:
        """Write (or refresh) the linked notes of a day in vault/notes; the person's edits stay."""
        from .notes import Notes

        writer = Notes(store)
        return {"notes": sorted(p.relative_to(writer.root).as_posix() for p in writer.write_day(date))}

    @app.get("/notes")
    def list_notes() -> list[Record]:
        """The notes folder as last read into the database: path, title, and the entity a
        generated note stands for."""
        store.sync_notes()
        with store.connect() as db:
            return [dict(r) for r in db.execute("SELECT path, title, identity FROM notes ORDER BY path")]

    @app.get("/moment/{moment_id}")
    def moment(moment_id: str, date: str = Query(...)) -> Record:
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

    @app.get("/vault/{date}/{asset_path:path}")
    def asset(date: str, asset_path: str) -> FileResponse:
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

    # Memories: photos you can step into (ingest/recon/memory.py), in their day's ``memories``.
    @app.get("/memories")
    def memories() -> list[Record]:
        """Newest first: each memory's day, id and what the viewer needs to show it."""
        out = []
        for meta_path in vault.root.glob("????-??-??/memories/mem_*/memory.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            keep = ("width", "height", "fx", "fov_y_deg", "splats", "depth_m", "model", "made", "source")
            date = meta_path.parents[2].name
            item = {"date": date, "id": meta_path.parent.name, **{k: meta.get(k) for k in keep}}
            # A photo from a scan stands in that scan's world: its splat (trained, else the LiDAR's)
            # and where the photo was taken in it.
            world = meta.get("world")
            if isinstance(world, dict) and re.fullmatch(r"[\w.-]+", str(world.get("moment", ""))):
                scene = read_json(vault.root / date / "moments" / world["moment"] / "scene.json") or {}
                splat = scene.get("splat") if isinstance(scene.get("splat"), dict) else {}
                name = splat.get("trained") or splat.get("lidar")
                if name:
                    item["world"] = {**world, "splat": f"moments/{world['moment']}/{name}", "trained": bool(splat.get("trained")),
                                     "bounds": splat.get("bounds")}  # fmt: skip
            out.append(item)
        return sorted(out, key=lambda m: (m["date"], str(m.get("made"))), reverse=True)

    @app.get("/memories/{date}/{ident}/{name}")
    def memory_file(date: str, ident: str, name: str) -> FileResponse:
        if (
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
            or not re.fullmatch(r"mem_[0-9a-f]{12}", ident)
            or name not in MEMORY_FILES
        ):
            raise HTTPException(404, "Memory not found")
        path = vault.safe_path(f"{date}/memories/{ident}/{name}")
        if not path.is_file():
            raise HTTPException(404, "Memory not found")
        return FileResponse(path, media_type=MEMORY_FILES[name], headers={"X-Content-Type-Options": "nosniff"})

    @app.post("/ask", dependencies=[Depends(authorize)], response_model=None)
    def answer(body: AskRequest, stream: bool = False) -> Record | StreamingResponse:
        result = ask(store, body.date, body.question.strip())
        if not stream:
            return result

        def generate() -> Iterator[str]:
            yield sse(
                "location",
                {key: result[key] for key in ("layer_index", "t", "floor", "x", "y", "moment_id", "offset_s")},
            )
            yield sse("answer", result)
            yield sse("done", {"status": "complete"})

        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/events", dependencies=[Depends(authorize)])
    def add_events(body: EventsRequest) -> Record:
        accepted = vault.append_events(
            body.date, [event.model_dump(mode="json", by_alias=True, exclude_unset=True) for event in body.events]
        )
        return {"accepted": accepted}

    def run_job(job_id: str, date: str) -> None:
        with jobs_lock:
            jobs[job_id].update(status="running", stage="index", pct=10)
        try:
            result = store.update(date)
        except Exception:
            log.exception("Index job failed for %s", date)
            with jobs_lock:
                jobs[job_id].update(status="failed", message="Index build failed; run daycube validate for diagnostics")
        else:
            with jobs_lock:
                jobs[job_id].update(status="complete", pct=100, result=result, message="Vault index updated")

    @app.post("/ingest/{date}", dependencies=[Depends(authorize)], status_code=202)
    def ingest_day(date: str, body: IngestRequest, tasks: BackgroundTasks) -> Record:
        vault.day_path(date)
        if set(body.steps) != {"index"}:
            raise HTTPException(501, "Only the index stage is implemented. Capture/moments/summaries are planned.")
        with jobs_lock:
            # Admission and registration are one operation: concurrent clients share work
            # already queued or running for this day, including when the queue is full.
            for key, state in jobs.items():
                if state["date"] == date and state["status"] in {"queued", "running"}:
                    return {"job_id": key}
            job_id = uuid4().hex
            if len(jobs) >= MAX_JOBS:
                finished = next((key for key, value in jobs.items() if value["status"] in {"complete", "failed"}), None)
                if finished:
                    del jobs[finished]
                else:
                    raise HTTPException(429, "Too many active jobs")
            jobs[job_id] = {
                "id": job_id,
                "date": date,
                "status": "queued",
                "stage": "index",
                "pct": 0,
                "message": "Queued",
                "created_at": datetime.now(UTC).isoformat(),
            }
            tasks.add_task(run_job, job_id, date)
        return {"job_id": job_id}

    def job_snapshot(job_id: str) -> Record | None:
        with jobs_lock:
            state = jobs.get(job_id)
            return dict(state) if state is not None else None

    @app.get("/jobs/{job_id}", response_model=None)
    def job(job_id: str, stream: bool = False) -> Record | StreamingResponse:
        state = job_snapshot(job_id)
        if state is None:
            raise HTTPException(404, "Job not found; jobs are process-local and expire on restart")
        if not stream:
            return state

        async def generate() -> AsyncIterator[str]:
            previous = ""
            while (state := job_snapshot(job_id)) is not None:
                serialized = json.dumps(state)
                if serialized != previous:
                    yield sse("progress", state)
                    previous = serialized
                if state["status"] in {"complete", "failed"}:
                    break
                await asyncio.sleep(0.2)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post("/places/rename", dependencies=[Depends(authorize)])
    def rename(body: RenameRequest) -> Record:
        if not body.name.strip():
            raise ValueError("Name must not be blank")
        if not vault.has_place(body.place_id):
            raise HTTPException(404, "Place not found")
        with vault.lock:
            names = read_json(vault.root / "places.json")
            names[body.place_id] = body.name.strip()
            vault.write_json(vault.root / "places.json", names)
        return {"ok": True}

    @app.post("/speakers/confirm", dependencies=[Depends(authorize)])
    def confirm(body: SpeakerRequest) -> Record:
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
        from .voices import update as spread_names

        spread_names(vault)
        return {"ok": True, "message": "Speaker named here and wherever their voice speaks"}

    @app.delete("/day/{date}/hours", dependencies=[Depends(authorize)])
    def delete_hours(date: str) -> None:
        vault.day_path(date)
        raise HTTPException(
            501, "Hour deletion is not implemented. Do not use this starter for personal recording yet."
        )

    def capture_control(action: str) -> Record:
        try:
            response = httpx.post(
                f"http://127.0.0.1:7700/{action}", headers={"Authorization": f"Bearer {vault.token}"}, timeout=2
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as error:
            raise HTTPException(503, "Capture receiver is not available") from error

    @app.post("/pause", dependencies=[Depends(authorize)])
    def pause() -> Record:
        return capture_control("pause")

    @app.post("/resume", dependencies=[Depends(authorize)])
    def resume() -> Record:
        return capture_control("resume")

    return app


app = create_app(warm=True)

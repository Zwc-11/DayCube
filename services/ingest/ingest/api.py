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
from .notes.read import DayNotes, attach_notes, read_note
from .store import EmbeddingModelChanged, LifeStore
from .time import instant
from .vault import Record, Vault, read_json

log = logging.getLogger("daycube.api")

MEDIA_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".svg", ".mp4", ".wav", ".glb", ".ply", ".spz"})
SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
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
    person_id: str = Field(pattern=r"^person:[A-Za-z0-9_-]+$")


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
        return {
            "meta": read_json(path / "meta.json"),
            "scene": read_json(path / "scene.json"),
            "transcript": read_json(path / "transcript.json"),
        }

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
            headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"},
        )

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
        return {"ok": True, "message": "Speaker map saved; quote event regeneration is not implemented yet"}

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

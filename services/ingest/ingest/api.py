from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .ask.service import ask
from .config import Settings
from .contracts import ContractError
from .generated.event import Event
from .index.embed import Embedder, default_embedder
from .store import LifeStore
from .vault import Record, Vault, read_json

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


def create_app(settings: Settings | None = None, *, embedder: Embedder | None = None) -> FastAPI:
    """``embedder`` replaces the configured model (tests pass a deterministic one)."""
    settings = settings or Settings()
    vault = Vault(settings.vault_root)
    store = LifeStore(
        vault, embedder or default_embedder(settings.vector_search, settings.embedding_model), zone=settings.timezone
    )
    jobs: dict[str, Record] = {}
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        vault.initialize()
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
        return vault.list_days()

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
        jobs[job_id].update(status="running", stage="index", pct=10)
        try:
            result = store.update(date)
            jobs[job_id].update(status="complete", pct=100, result=result, message="Vault index updated")
        except Exception:
            jobs[job_id].update(status="failed", message="Index build failed; run daycube validate for diagnostics")

    @app.post("/ingest/{date}", dependencies=[Depends(authorize)], status_code=202)
    def ingest_day(date: str, body: IngestRequest, tasks: BackgroundTasks) -> Record:
        vault.day_path(date)
        if set(body.steps) != {"index"}:
            raise HTTPException(501, "Only the index stage is implemented. Capture/moments/summaries are planned.")
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

    @app.get("/jobs/{job_id}", response_model=None)
    def job(job_id: str, stream: bool = False) -> Record | StreamingResponse:
        if job_id not in jobs:
            raise HTTPException(404, "Job not found; jobs are process-local and expire on restart")
        if not stream:
            return jobs[job_id]

        async def generate() -> AsyncIterator[str]:
            previous = ""
            while job_id in jobs:
                state = dict(jobs[job_id])
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


app = create_app()

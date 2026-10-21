import asyncio
import json
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jsonschema import ValidationError
from pydantic import BaseModel, ConfigDict, Field

from .ask.service import ask
from .config import Settings
from .generated.event import Event
from .index.build import build_index
from .time import instant, overlaps
from .vault import Vault, read_json


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AskRequest(RequestModel):
    date: str
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


def sse(event: str, value: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(value)}\n\n"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    vault = Vault(settings.vault_root)
    jobs: dict[str, dict] = {}
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        vault.initialize()
        yield

    app = FastAPI(title="DAYCUBE Local API", version="0.1.0", lifespan=lifespan,
                  description="Local vault and extractive search. Synthetic fixture data is labeled illustrative.")
    app.state.vault = vault
    origins = settings.cors_origins.copy()
    if settings.extension_id:
        origins.append(f"chrome-extension://{settings.extension_id}")
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if credentials is None or not secrets.compare_digest(credentials.credentials, vault.token):
            raise HTTPException(401, "A valid vault bearer token is required", headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(FileNotFoundError)
    async def missing(request: Request, error: FileNotFoundError):
        return JSONResponse(status_code=404, content={"detail": "The requested vault record was not found"})

    @app.exception_handler(ValueError)
    async def invalid(request: Request, error: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.exception_handler(ValidationError)
    async def contract_error(request: Request, error: ValidationError):
        return JSONResponse(status_code=422, content={"detail": error.message, "path": list(error.absolute_path)})

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "timezone": settings.timezone,
                "capabilities": {"vault": True, "fts_search": True, "ask_mode": "extractive",
                                 "vector_search": False, "capture": False, "gpu_pipeline": False,
                                 "delete_hours": False}}

    @app.get("/days")
    def days():
        return vault.list_days()

    @app.get("/day/{date}")
    def day(date: str):
        return vault.load_day(date)

    @app.get("/day/{date}/events")
    def events(date: str, t0: str | None = None, t1: str | None = None,
               type: str | None = None):
        if bool(t0) != bool(t1):
            raise ValueError("Provide both t0 and t1, or neither")
        if t0 and t1 and instant(t0) >= instant(t1):
            raise ValueError("t1 must be after t0")
        return [e for e in vault.events(date)
                if (type is None or e["type"] == type)
                and (not t0 or overlaps(e["t"], e.get("t_end"), t0, t1))]

    @app.get("/day/{date}/skeleton")
    def skeleton(date: str):
        return read_json(vault.day_path(date) / "skeleton.geojson")

    @app.get("/day/{date}/graph")
    def graph(date: str):
        return read_json(vault.day_path(date) / "graph.json")

    @app.get("/moment/{moment_id}")
    def moment(moment_id: str, date: str = Query(...)):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", moment_id):
            raise ValueError("Invalid moment id")
        path = vault.day_path(date) / "moments" / moment_id
        return {"meta": read_json(path / "meta.json"), "scene": read_json(path / "scene.json"),
                "transcript": read_json(path / "transcript.json")}

    @app.get("/vault/{date}/{asset_path:path}")
    def asset(date: str, asset_path: str):
        day_path = vault.day_path(date)
        candidate = (day_path / asset_path).resolve()
        if not candidate.is_relative_to(day_path) or candidate.suffix.lower() not in {
            ".jpg", ".jpeg", ".png", ".svg", ".mp4", ".wav", ".glb", ".ply", ".spz"
        } or not candidate.is_file():
            raise HTTPException(404, "Asset not found")
        return FileResponse(candidate, headers={"X-Content-Type-Options": "nosniff",
                                               "Content-Security-Policy": "default-src 'none'; sandbox"})

    @app.post("/ask", dependencies=[Depends(authorize)])
    def answer(body: AskRequest, stream: bool = False):
        result = ask(vault, body.date, body.question.strip())
        if not stream:
            return result

        def generate():
            yield sse("location", {key: result[key] for key in (
                "layer_index", "t", "floor", "x", "y", "moment_id", "offset_s")})
            yield sse("answer", result)
            yield sse("done", {"status": "complete"})

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.post("/events", dependencies=[Depends(authorize)])
    def add_events(body: EventsRequest):
        accepted = vault.append_events(body.date, [event.model_dump(mode="json", by_alias=True, exclude_unset=True)
                                                  for event in body.events])
        return {"accepted": accepted}

    def run_job(job_id: str, date: str):
        jobs[job_id].update(status="running", stage="index", pct=10)
        try:
            result = build_index(vault, date)
            jobs[job_id].update(status="complete", pct=100, result=result,
                                message="Local FTS5 index rebuilt")
        except Exception:
            jobs[job_id].update(status="failed", message="Index build failed; run daycube validate for diagnostics")

    @app.post("/ingest/{date}", dependencies=[Depends(authorize)], status_code=202)
    def ingest_day(date: str, body: IngestRequest, tasks: BackgroundTasks):
        vault.day_path(date)
        if set(body.steps) != {"index"}:
            raise HTTPException(501, "Only the index stage is implemented. Capture/moments/summaries are planned.")
        job_id = uuid4().hex
        if len(jobs) >= 100:
            finished = next((key for key, value in jobs.items() if value["status"] in {"complete", "failed"}), None)
            if finished:
                del jobs[finished]
            else:
                raise HTTPException(429, "Too many active jobs")
        jobs[job_id] = {"id": job_id, "date": date, "status": "queued", "stage": "index", "pct": 0,
                        "message": "Queued", "created_at": datetime.now(timezone.utc).isoformat()}
        tasks.add_task(run_job, job_id, date)
        return {"job_id": job_id}

    @app.get("/jobs/{job_id}")
    def job(job_id: str, stream: bool = False):
        if job_id not in jobs:
            raise HTTPException(404, "Job not found; jobs are process-local and expire on restart")
        if not stream:
            return jobs[job_id]

        async def generate():
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
    def rename(body: RenameRequest):
        if not body.name.strip():
            raise ValueError("Name must not be blank")
        if not any(body.place_id in {p["id"] for p in read_json(vault.day_path(d["date"]) / "day.json")["places"]}
                   for d in vault.list_days()):
            raise HTTPException(404, "Place not found")
        with vault.lock:
            names = read_json(vault.root / "places.json")
            names[body.place_id] = body.name.strip()
            vault.write_json(vault.root / "places.json", names)
        return {"ok": True}

    @app.post("/speakers/confirm", dependencies=[Depends(authorize)])
    def confirm(body: SpeakerRequest):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", body.moment_id):
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
    def delete_hours(date: str):
        vault.day_path(date)
        raise HTTPException(501, "Hour deletion is not implemented. Do not use this starter for personal recording yet.")

    def capture_control(action: str):
        try:
            response = httpx.post(f"http://127.0.0.1:7700/{action}",
                                  headers={"Authorization": f"Bearer {vault.token}"}, timeout=2)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as error:
            raise HTTPException(503, "Capture receiver is not available") from error

    @app.post("/pause", dependencies=[Depends(authorize)])
    def pause():
        return capture_control("pause")

    @app.post("/resume", dependencies=[Depends(authorize)])
    def resume():
        return capture_control("resume")

    return app


app = create_app()

"""Index jobs: bring a day's search records up to date in the background, and follow one."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import Field

from ..store import LifeStore
from ..vault import Record
from .deps import AUTHORIZED, RequestModel, TheStore, TheVault, state
from .search import sse

log = logging.getLogger("daycube.api")
router = APIRouter()
MAX_JOBS = 100
ACTIVE, FINISHED = {"queued", "running"}, {"complete", "failed"}


class IngestRequest(RequestModel):
    steps: list[str] = Field(default_factory=lambda: ["index"], min_length=1)


class Jobs:
    """This process's index jobs; they are gone on restart."""

    def __init__(self) -> None:
        self.jobs: dict[str, Record] = {}
        self.lock = threading.Lock()

    def admit(self, date: str, tasks: BackgroundTasks, store: LifeStore) -> str:
        """The job for ``date``: the one already queued or running, else a new one. Admission and
        registration are one operation, so concurrent clients share work, also when the queue is full."""
        with self.lock:
            for key, job in self.jobs.items():
                if job["date"] == date and job["status"] in ACTIVE:
                    return key
            if len(self.jobs) >= MAX_JOBS:
                finished = next((key for key, job in self.jobs.items() if job["status"] in FINISHED), None)
                if finished is None:
                    raise HTTPException(429, "Too many active jobs")
                del self.jobs[finished]
            job_id = uuid4().hex
            self.jobs[job_id] = {
                "id": job_id,
                "date": date,
                "status": "queued",
                "stage": "index",
                "pct": 0,
                "message": "Queued",
                "created_at": datetime.now(UTC).isoformat(),
            }
            tasks.add_task(self.run, job_id, date, store)
            return job_id

    def update(self, job_id: str, **changes: object) -> None:
        with self.lock:
            self.jobs[job_id].update(changes)

    def run(self, job_id: str, date: str, store: LifeStore) -> None:
        self.update(job_id, status="running", stage="index", pct=10)
        try:
            result = store.update(date)
        except Exception:
            log.exception("Index job failed for %s", date)
            self.update(job_id, status="failed", message="Index build failed; run daycube validate for diagnostics")
        else:
            self.update(job_id, status="complete", pct=100, result=result, message="Vault index updated")

    def get(self, job_id: str) -> Record | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job is not None else None


TheJobs = Annotated[Jobs, state("jobs")]


@router.post("/ingest/{date}", dependencies=AUTHORIZED, status_code=202)
def ingest_day(
    date: str, body: IngestRequest, tasks: BackgroundTasks, vault: TheVault, store: TheStore, jobs: TheJobs
) -> Record:
    vault.day_path(date)
    if set(body.steps) != {"index"}:
        raise HTTPException(501, "Only the index stage is implemented. Capture/moments/summaries are planned.")
    return {"job_id": jobs.admit(date, tasks, store)}


@router.get("/jobs/{job_id}", response_model=None)
def job(job_id: str, jobs: TheJobs, stream: bool = False) -> Record | StreamingResponse:
    found = jobs.get(job_id)
    if found is None:
        raise HTTPException(404, "Job not found; jobs are process-local and expire on restart")
    if not stream:
        return found

    async def generate() -> AsyncIterator[str]:
        previous = ""
        while (current := jobs.get(job_id)) is not None:
            serialized = json.dumps(current)
            if serialized != previous:
                yield sse("progress", current)
                previous = serialized
            if current["status"] in FINISHED:
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(generate(), media_type="text/event-stream")

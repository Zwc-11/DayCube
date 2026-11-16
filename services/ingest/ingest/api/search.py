"""Finding things across days: search and Ask, and the graph of what the records name."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date as date_type
from datetime import datetime
from datetime import time as time_type
from time import perf_counter
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import Field

from ..ask.service import ask
from ..notes.read import attach_notes
from ..time import instant
from ..vault import Record
from .deps import AUTHORIZED, RequestModel, TheSettings, TheStore

router = APIRouter()


class AskRequest(RequestModel):
    date: str | None = None  # None: search every day in the vault
    question: str = Field(min_length=1, max_length=2000)


def sse(event: str, value: Record) -> str:
    return f"event: {event}\ndata: {json.dumps(value)}\n\n"


def epoch(value: str | None, zone: str) -> float | None:
    """A date (local midnight in ``zone``) or an ISO time, as epoch seconds."""
    if not value:
        return None
    if len(value) == 10:
        return datetime.combine(date_type.fromisoformat(value), time_type(), ZoneInfo(zone)).timestamp()
    return instant(value).timestamp()


@router.get("/search")
def search(store: TheStore, q: str = Query(..., min_length=1, max_length=500), date: str | None = None) -> Record:
    """Ask without the vault token, for search as you type: the same extractive answer and
    evidence as ``POST /ask``, each result with the note it is in (``note``) and the words to
    find there (``match``), and how long it took (``took_ms``). Reads only, like the other
    GET routes."""
    started = perf_counter()
    result = ask(store, date, q.strip())
    attach_notes(store, result["evidence"])
    result["took_ms"] = round((perf_counter() - started) * 1000, 1)
    return result


@router.post("/ask", dependencies=AUTHORIZED, response_model=None)
def answer(body: AskRequest, store: TheStore, stream: bool = False) -> Record | StreamingResponse:
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


@router.get("/graph")
def knowledge_graph(
    store: TheStore,
    settings: TheSettings,
    since: str | None = None,
    until: str | None = None,
    kinds: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
) -> Record:
    """Entities the records name (sites, pages, searches, courses, deadlines, places, people)
    most mentioned between ``since`` and ``until``, and the links among them."""
    zone = settings.timezone
    return store.graph(
        since=epoch(since, zone), until=epoch(until, zone), kinds=kinds.split(",") if kinds else None, limit=limit
    )


@router.get("/entity/{entity_id}")
def entity(entity_id: int, store: TheStore, limit: int = Query(50, ge=1, le=500)) -> Record:
    found = store.entity(entity_id, limit=limit)
    if found is None:
        raise FileNotFoundError(entity_id)
    return found

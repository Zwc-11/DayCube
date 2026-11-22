"""The vault's local API (docs/api.md): one FastAPI app, its routes grouped by what they serve.

days     a day's records: listing, reading, correcting
search   search, Ask and the knowledge graph
notes    the Markdown notes and their graph
review   the end-of-day review and its labels
forget   deleting a span of a day
scenes   memories, worlds, items and the map
recall   episodes, the morning's questions and cue cards, and how the day felt
jobs     index jobs
system   what the vault holds, training runs, the eye tracker's learning, pausing capture
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..ask.retrieve import retrieve
from ..config import Settings
from ..contracts import ContractError
from ..index.embed import Embedder, default_embedder
from ..integrations.overpass import OverpassAdapter
from ..notes.graph import NoteGraph
from ..notes.read import DayNotes
from ..store import EmbeddingModelChanged, LifeStore
from ..vault import Vault
from . import days, forget, jobs, notes, recall, review, scenes, search, system

log = logging.getLogger("daycube.api")
FILL_EVERY_S = 60.0  # how often a warm API looks for days stored without their vectors


def warm_up(store: LifeStore, stop: threading.Event) -> None:
    """Bring the vault database up to date and load the embedding model and vector index, so the
    first question is answered as fast as the rest; then, while the API runs, embed the days
    stored without vectors, so meaning search covers every day."""
    started = perf_counter()
    try:
        store.ensure()
        retrieve(store, "warm up the search")
    except Exception:  # noqa: BLE001 - a cold first question is the only cost
        log.exception("Warming up search failed")
    else:
        log.info("Search warm in %.1f s", perf_counter() - started)
    while not stop.wait(FILL_EVERY_S):
        try:
            if filled := store.fill_backlog(stop):
                log.info("Meaning search: %d more texts embedded", filled)
        except Exception:  # noqa: BLE001 - tried again later; keywords answer meanwhile
            log.exception("Embedding the vault's backlog failed")


def create_app(settings: Settings | None = None, *, embedder: Embedder | None = None, warm: bool = False) -> FastAPI:
    """``embedder`` replaces the configured model (tests pass a deterministic one). ``warm`` runs
    ``warm_up`` in the background from startup."""
    settings = settings or Settings()
    vault = Vault(settings.vault_root)
    store = LifeStore(
        vault, embedder or default_embedder(settings.vector_search, settings.embedding_model), zone=settings.timezone
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        vault.initialize()
        stop = threading.Event()
        if warm:
            threading.Thread(target=warm_up, args=(store, stop), name="warm-up", daemon=True).start()
        yield
        stop.set()

    app = FastAPI(
        title="DAYCUBE Local API",
        version="0.1.0",
        lifespan=lifespan,
        description="Local vault and extractive search. Synthetic fixture data is labeled illustrative.",
    )
    # What the routes depend on (api/deps.py ``state``).
    app.state.settings = settings
    app.state.vault = vault
    app.state.store = store
    app.state.day_notes = DayNotes(store)
    app.state.note_graph = NoteGraph(store, app.state.day_notes)
    app.state.writers = review.Writers.of(settings)
    app.state.jobs = jobs.Jobs()
    app.state.overpass = OverpassAdapter(vault.root / "maps" / "osm")

    origins = settings.cors_origins.copy()
    if settings.extension_id:
        origins.append(f"chrome-extension://{settings.extension_id}")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

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

    for module in (days, search, notes, review, forget, scenes, recall, jobs, system):
        app.include_router(module.router)
    return app


app = create_app(warm=True)

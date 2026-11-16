"""The vault's Markdown notes (vault/notes): a day's notes, one note, and the graph of links."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ..notes.graph import NoteGraph
from ..notes.read import DayNotes, read_note
from ..vault import Record
from .deps import AUTHORIZED, TheStore, conditional, state

router = APIRouter()


@router.get("/day/{date}/notes")
def knowledge(date: str, day_notes: Annotated[DayNotes, state("day_notes")]) -> Record:
    """The day's knowledge base: its daily note (written or refreshed now if its records changed),
    the notes it links to and that link to it, and its local graph."""
    return day_notes.view(date)


@router.get("/notes/graph")
def notes_graph(request: Request, note_graph: Annotated[NoteGraph, state("note_graph")]) -> Response:
    """Every note in vault/notes (path, kind) and every [[link]] between them, links to notes
    not written yet as their own nodes (``missing``): the vault's graph. Every recorded day gets
    its daily note first. Tagged: a client holding the current copy gets an empty 304."""
    return conditional(request, *note_graph.tagged())


@router.get("/note")
def note(store: TheStore, path: str = Query(..., max_length=1000)) -> Record:
    """One note in vault/notes: its Markdown, its links (each with the note it opens) and backlinks."""
    return read_note(store, path)


@router.get("/notes")
def list_notes(store: TheStore) -> list[Record]:
    """The notes folder as last read into the database: path, title, and the entity a
    generated note stands for."""
    store.sync_notes()
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT path, title, identity FROM notes ORDER BY path")]


@router.post("/notes/{date}", dependencies=AUTHORIZED)
def write_notes(date: str, store: TheStore) -> Record:
    """Write (or refresh) the linked notes of a day in vault/notes; the person's edits stay."""
    from ..notes import Notes

    writer = Notes(store)
    return {"notes": sorted(p.relative_to(writer.root).as_posix() for p in writer.write_day(date))}

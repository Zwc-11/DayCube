"""The end-of-day review (ingest/review, docs/review.md): reading it, writing it now, correcting
the categories it is made from, and the morning notification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import Field

from ..config import Settings
from ..store import LifeStore
from ..vault import Record
from .deps import AUTHORIZED, Now, RequestModel, TheSettings, TheStore, TheVault, state

router = APIRouter()


@dataclass(frozen=True)
class Writers:
    """Who words the review and labels unknown apps and sites: a language model if one is turned
    on, else the template and the rules (the default: nothing leaves this machine)."""

    model: Any
    labeler: Any
    by: dict[str, str]  # shown in the viewer: {"summary": ..., "categories": ...}

    @classmethod
    def of(cls, settings: Settings) -> Writers:
        from ..integrations.models import language_model
        from ..review.labels import llm_labeler

        model, model_by = language_model(settings.summary_llm, "DAYCUBE_SUMMARY_LLM")
        labeler, labels_by = llm_labeler(settings.category_llm)
        return cls(
            model, labeler, {"summary": model_by if model else f"template ({model_by})", "categories": labels_by}
        )


TheWriters = Annotated[Writers, state("writers")]


class LabelRequest(RequestModel):
    """A correction of what a window or page, or a whole app or site, is (ingest/review/labels.py).
    ``category`` absent: unchanged; ``project`` null: no project; ``clear``: back to the rules."""

    date: str
    key: str = Field(min_length=5, max_length=2200)
    category: str | None = Field(default=None, max_length=32)
    project: str | None = Field(default=None, max_length=80)
    clear: bool = False


def refresh_daily(store: LifeStore, date: str) -> None:
    """The day's daily note shows its review at the top: written again with it."""
    if store.knows(date):
        from ..notes import Notes

        Notes(store).write_day(date)


@router.get("/day/{date}/review")
def day_review(date: str, vault: TheVault, settings: TheSettings, writers: TheWriters, now: Now) -> Record:
    """The day's end-of-day review (null until one is written), its timeline (written now if
    the day has none yet), the categories to relabel with, and who words the review."""
    from ..review.summary import view

    vault.day_path(date, must_exist=False)  # a valid date, or 400
    return view(vault, date, settings.timezone, now=now, writers=writers.by)


@router.post("/day/{date}/review", dependencies=AUTHORIZED)
def summarize_day(
    date: str, vault: TheVault, store: TheStore, settings: TheSettings, writers: TheWriters, now: Now
) -> Record:
    """Write the day's review now (a draft while the day is under way), then return the view."""
    from ..review.summary import view, write_review

    vault.day_path(date, must_exist=False)
    zone = settings.timezone
    out = write_review(vault, store, date, zone, now=now, model=writers.model, labeler=writers.labeler, force=True)
    if out["review"] is not None:
        refresh_daily(store, date)
    return view(vault, date, zone, now=now, writers=writers.by) | {
        "result": {"written": out["written"], "why": out["why"]}
    }


@router.post("/labels", dependencies=AUTHORIZED)
def relabel(
    body: LabelRequest, vault: TheVault, store: TheStore, settings: TheSettings, writers: TheWriters, now: Now
) -> Record:
    """Correct a category or project, rebuild that day's timeline, and write its review again
    if it has one and its facts changed. Other days follow when they are next written."""
    from ..review import timeline as day_timeline
    from ..review.labels import UNSET, correct
    from ..review.summary import day_zone, read_review, view, write_review

    vault.day_path(body.date, must_exist=False)
    given = body.model_fields_set
    correct(
        vault, body.key, clear=body.clear,
        category=body.category if "category" in given and body.category is not None else UNSET,
        project=body.project if "project" in given else UNSET,
    )  # fmt: skip
    if read_review(vault, body.date) is not None:
        out = write_review(
            vault, store, body.date, settings.timezone, now=now, model=writers.model, labeler=writers.labeler
        )
        if out["written"]:
            refresh_daily(store, body.date)
    elif day_timeline.has_inputs(vault, body.date):
        zone = day_zone(vault, body.date, settings.timezone)
        under_way = now < day_timeline.window(body.date, ZoneInfo(zone))[1]
        day_timeline.write(vault, body.date, zone, now=now if under_way else None, labeler=writers.labeler)
    return view(vault, body.date, settings.timezone, now=now, writers=writers.by)


@router.get("/morning")
def morning(vault: TheVault, settings: TheSettings, now: Now) -> Record:
    """Yesterday's final review as the desktop app's morning notification shows it, from
    ``DAYCUBE_PUSH_AT`` (08:00) local time; ``date`` null before then or without one."""
    from ..review.push import due, message

    found = due(vault, settings, now, settings.timezone)
    if found is None:
        return {"date": None}
    title, text = message(found[1])
    return {"date": found[0], "title": title, "text": text}

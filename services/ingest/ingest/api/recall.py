"""Remembering the day (ingest/recall, docs/recall.md) and how it felt (ingest/feeling.py,
docs/feeling.md): a day's episodes with their strength, the morning's questions and the day's
cue cards, and feeling reports beside their evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import Field

from .. import feeling
from ..recall import episodes, history, today
from ..review import timeline
from ..vault import Record
from .deps import AUTHORIZED, Now, RequestModel, TheSettings, TheStore, TheVault

router = APIRouter()


class AnswerRequest(RequestModel):
    """The person's own verdict on one of today's questions, given after trying to recall."""

    question: str = Field(max_length=40)
    outcome: Literal["remembered", "partly", "forgot"]


class OpenedRequest(RequestModel):
    """An episode opened from a card or a search: a recall that asked nothing."""

    date: str
    episode: str = Field(max_length=40)


class FeelingRequest(RequestModel):
    """A feeling report: energy and mood from 1 (low) to 5 (high), with an optional note.
    ``t`` (with its UTC offset) defaults to now."""

    energy: int = Field(ge=1, le=5)
    mood: int = Field(ge=1, le=5)
    note: str | None = Field(default=None, max_length=280)
    t: datetime | None = None


@router.get("/day/{date}/episodes")
def day_episodes(date: str, vault: TheVault, store: TheStore, settings: TheSettings, now: Now) -> Record:
    """The review day's episodes, each with its strength now. ``written`` false: the nightly
    round has not written them yet, so they were made for this reply from the timeline so far."""
    vault.day_path(date, must_exist=False)  # a valid date, or 400
    found = episodes.read(vault, date)
    made = found if found is not None else episodes.build(vault, store, date, settings.timezone)
    return {"date": date, "written": found is not None, "episodes": history.with_strength(vault, made, now)}


def _asked(vault: TheVault, settings: TheSettings, now: Now) -> Record:
    day = timeline.review_day(now, ZoneInfo(settings.timezone))
    return today.questions(vault, day, now) | {"cards": today.cards(vault, day, now)}


@router.get("/recall/today")
def recall_today(vault: TheVault, settings: TheSettings, now: Now) -> Record:
    """Today's recall questions (chosen at the first request of the review day, then kept), the
    answers given so far, and up to three cue cards."""
    return _asked(vault, settings, now)


@router.post("/recall/answer", dependencies=AUTHORIZED)
def recall_answer(body: AnswerRequest, vault: TheVault, settings: TheSettings, now: Now) -> Record:
    """Grade one of today's questions; the first verdict counts. Returns today as ``/recall/today``."""
    today.answer(vault, timeline.review_day(now, ZoneInfo(settings.timezone)), body.question, body.outcome, now)
    return _asked(vault, settings, now)


@router.post("/recall/opened", dependencies=AUTHORIZED)
def recall_opened(body: OpenedRequest, vault: TheVault, now: Now) -> Record:
    """Record that an episode was opened (404 if the day has no such episode)."""
    vault.day_path(body.date, must_exist=False)
    return today.opened(vault, body.date, body.episode, now)


@router.get("/recall")
def recall_status(vault: TheVault) -> Record:
    """Recalls by outcome, the share of graded questions that came back, and the weights in use."""
    return history.status(vault)


@router.post("/feeling", dependencies=AUTHORIZED)
def report_feeling(body: FeelingRequest, vault: TheVault, settings: TheSettings, now: Now) -> Record:
    """Keep a feeling report; returns it with the evidence recorded in the ten minutes before."""
    if body.t is not None and (body.t.tzinfo is None or body.t > now):
        raise ValueError("A report's time needs its UTC offset and cannot be after now")
    t = (body.t or now).astimezone(ZoneInfo(settings.timezone))
    return feeling.report(vault, t, body.energy, body.mood, body.note) | {
        "evidence": feeling.evidence(vault, t, settings.timezone)
    }


@router.get("/day/{date}/feeling")
def day_feeling(date: str, vault: TheVault, settings: TheSettings) -> Record:
    """The calendar day's reports, each beside its evidence."""
    vault.day_path(date, must_exist=False)
    zone = settings.timezone
    found = feeling.reports(vault, date)
    return {
        "date": date,
        "reports": [r | {"evidence": feeling.evidence(vault, datetime.fromisoformat(r["t"]), zone)} for r in found],
    }


@router.get("/feeling/model")
def feeling_model(vault: TheVault, settings: TheSettings) -> Record:
    """Where the personal model stands: reports so far, and once there are enough, its error on
    left-out days beside the person's own average, and whether it is kept or dropped."""
    return feeling.judge(feeling.gathered(vault, settings.timezone))

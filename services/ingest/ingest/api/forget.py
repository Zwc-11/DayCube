"""Deleting a span of a day (ingest/forget.py, docs/capture.md "Deleting a span") from the vault
and from everything made from it."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta
from datetime import time as time_type
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from ..config import Settings
from ..store import LifeStore
from ..vault import Record, Vault, overlaps
from .deps import AUTHORIZED, Now, RequestModel, TheSettings, TheStore, TheVault
from .review import TheWriters, Writers

router = APIRouter()


class HoursRequest(RequestModel):
    """A span of the day to delete (ingest/forget.py), as times with their UTC offset."""

    t0: datetime
    t1: datetime
    reason: str | None = Field(default=None, max_length=200)


def delete_span(
    vault: Vault, store: LifeStore, settings: Settings, writers: Writers, now: datetime, date: str, body: HoursRequest
) -> Record:
    """What ``DELETE /day/{date}/hours`` does; ``DELETE /recent`` calls it a day at a time."""
    from ..forget import forget
    from ..notes import Notes
    from ..review import timeline as day_timeline
    from ..review import weekly
    from ..review.summary import FILE as REVIEW
    from ..review.summary import day_zone, read_review, write_review

    vault.day_path(date, must_exist=False)
    zone_name = day_zone(vault, date, settings.timezone)
    zone = ZoneInfo(zone_name)
    first = date_type.fromisoformat(date)
    start, end = (datetime.combine(d, time_type(0), zone) for d in (first, first + timedelta(days=1)))
    if body.t0.tzinfo is None or body.t1.tzinfo is None or not start <= body.t0 < body.t1 <= end:
        raise HTTPException(422, f"The span must start before it ends, within {date}, with UTC offsets")
    span = (body.t0, body.t1)
    days = [d.isoformat() for d in (first - timedelta(days=1), first)]
    days = [d for d in days if overlaps(*day_timeline.window(d, zone), span)]  # review days: 04:00 to 04:00
    reviewed = {d for d in days if read_review(vault, d) is not None}
    notes = Notes(store)
    touched = notes.touched(date, span) if store.knows(date) else set()
    try:
        report = forget(vault, date, span, reason=body.reason)
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    try:
        store.update(date)
    except FileNotFoundError:
        store.ensure()  # nothing of the day is left: its rows go
    for day in days:
        for derived in ("timeline.jsonl", REVIEW):
            (vault.day_path(day, must_exist=False) / derived).unlink(missing_ok=True)
        if day in reviewed:
            write_review(
                vault, store, day, settings.timezone, now=now, model=writers.model, labeler=writers.labeler, force=True
            )
        elif day_timeline.has_inputs(vault, day):
            under_way = now < day_timeline.window(day, zone)[1]
            day_timeline.write(vault, day, zone_name, now=now if under_way else None, labeler=writers.labeler)
        year, week, _ = date_type.fromisoformat(day).isocalendar()
        if weekly.path_of(vault, f"{year}-W{week:02d}").exists():
            weekly.write(vault, store, day, zone_name, now=now)
    for day in sorted({date} | reviewed):
        try:
            notes.write_day(day)
        except FileNotFoundError:
            notes.rewrite({f"day {day}"})  # nothing left to write: the generated part goes
    notes.rewrite(touched)
    store.sync_notes()
    store.purge()
    return report


@router.delete("/day/{date}/hours", dependencies=AUTHORIZED)
def delete_hours(
    date: str,
    body: HoursRequest,
    vault: TheVault,
    store: TheStore,
    settings: TheSettings,
    writers: TheWriters,
    now: Now,
) -> Record:
    """Delete a span of the day from the vault (ingest/forget.py), then write again what was made
    from it: the search database (rewritten, so no page of the file keeps deleted text), the
    notes it touched, and the timelines, reviews and week of the review days it falls in."""
    return delete_span(vault, store, settings, writers, now, date, body)


@router.delete("/recent", dependencies=AUTHORIZED)
def delete_recent(
    vault: TheVault,
    store: TheStore,
    settings: TheSettings,
    writers: TheWriters,
    now: Now,
    minutes: int = Query(60, ge=1, le=24 * 60),
) -> Record:
    """Delete the last ``minutes`` up to now (the tray's Delete the last hour), a day at a time."""
    start, deleted = now - timedelta(minutes=minutes), []
    while start < now:
        midnight = datetime.combine(start.date() + timedelta(days=1), time_type(0), start.tzinfo)
        end = min(now, midnight)
        span = HoursRequest(t0=start, t1=end)
        deleted.append(delete_span(vault, store, settings, writers, now, start.date().isoformat(), span))
        start = end
    return {"deleted": deleted}


@router.get("/day/{date}/deletions")
def day_deletions(date: str, vault: TheVault) -> list[Record]:
    """The spans deleted from the day: when, why, and how many records of each kind went."""
    from ..forget import deletions

    vault.day_path(date, must_exist=False)
    return deletions(vault, date)

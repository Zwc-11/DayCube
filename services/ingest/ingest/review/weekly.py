"""The week reviewed: Monday to Sunday, from the days' timelines and reviews, in numbers only.

``vault/weeks/<YYYY-Www>.json`` (contract ``packages/schemas/week.schema.json``) and the note
``notes/Weekly/<YYYY-Www>.md``: a row per day (desk time, stretches of 25 min or more, the longest,
the main category and project, phone time and pickups, commits, keys), the week's totals, its time
per category and per project, against the week before, the hours the long stretches ran in, and the
deadlines of the week after. Written by the capture receiver once Sunday's review day has ended
(and again when a day of the week changes), or by ``daycube week``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import contracts
from ..store import LifeStore
from ..time import instant
from ..vault import Record, Vault, file_stamp, read_text, stored
from . import plan, timeline
from .labels import OTHER
from .summary import read_review

SCHEMA = "daycube.week/1"
FOCUS_S = 25 * 60.0
REVIEW_KEYS = ("phone", "pickups", "commits", "keys")


def week_key(day: str) -> tuple[str, date]:
    """The ISO week of a date, ``2026-W44``, and its Monday."""
    d = date.fromisoformat(day)
    year, week, weekday = d.isocalendar()
    return f"{year}-W{week:02d}", d - timedelta(days=weekday - 1)


def _day_row(vault: Vault, day: str) -> Record:
    segments = timeline.read(vault, day) or []
    desk = [s for s in segments if s["where"] == "desk"]
    seconds = [(instant(s["until"]) - instant(s["t"])).total_seconds() for s in desk]
    by_category: dict[str, float] = defaultdict(float)
    by_project: dict[str, float] = defaultdict(float)
    for s, length in zip(desk, seconds, strict=True):
        by_category[s["category"]] += length
        if s["project"]:
            by_project[s["project"]] += length
    review = read_review(vault, day)
    values = {n["key"]: n["value"] for n in (review or {}).get("numbers", [])}
    focus = [x for x in seconds if x >= FOCUS_S]
    shown = {k: v for k, v in by_category.items() if k not in (OTHER, "private")}
    return {
        "date": day,
        "desk_s": round(sum(seconds), 1),
        "focus": len(focus),
        "focus_s": round(sum(focus), 1),
        "longest_s": round(max(seconds, default=0.0), 1),
        "category": max(shown, key=lambda k: shown[k]) if shown else None,
        "project": max(by_project, key=lambda k: by_project[k]) if by_project else None,
        **{f"{k}": values.get(k) for k in REVIEW_KEYS},
        "review": review["kind"] if review else None,
        "_categories": dict(by_category),
        "_projects": dict(by_project),
    }


def _shares(rows: list[Record], field: str) -> list[Record]:
    total: dict[str, float] = defaultdict(float)
    for row in rows:
        for name, seconds in row[field].items():
            total[name] += seconds
    whole = sum(total.values())
    return [{"name": k, "seconds": round(v, 1), "share": round(v / whole, 4) if whole else 0.0}
            for k, v in sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))]  # fmt: skip


def collect(vault: Vault, store: LifeStore | None, day: str, zone: str) -> Record:
    """The week ``day`` is in."""
    key, monday = week_key(day)
    tz = ZoneInfo(zone)
    days = [(monday + timedelta(days=n)).isoformat() for n in range(7)]
    rows = [_day_row(vault, d) for d in days]
    before = [_day_row(vault, (monday - timedelta(days=7 - n)).isoformat()) for n in range(7)]

    def total(group: list[Record], field: str) -> float:
        return round(sum(r[field] or 0 for r in group), 1)

    totals = {f: total(rows, f) for f in ("desk_s", "focus", "focus_s")}
    totals |= {k: (total(rows, k) if any(r[k] is not None for r in rows) else None) for k in REVIEW_KEYS}
    totals["days_recorded"] = sum(1 for r in rows if r["desk_s"] > 0)
    previous = {f: total(before, f) for f in ("desk_s", "focus", "focus_s")} if any(r["desk_s"] for r in before) else None
    hours = plan.best_hours(vault, days[-1], tz, days=7)
    upcoming = []
    if store is not None and store.path.exists():
        first, last = (monday + timedelta(days=7)).isoformat(), (monday + timedelta(days=13)).isoformat()
        seen: set[str] = set()
        for row in store.deadlines(first, last, days[-1]):
            if row["key"] not in seen:
                seen.add(row["key"])
                upcoming.append({"text": row["name"], "due": row["key"].split("|")[0]})
    return {
        "schema": SCHEMA, "week": key, "from": days[0], "to": days[-1], "zone": zone,
        "days": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
        "totals": totals,
        "categories": _shares(rows, "_categories"), "projects": _shares(rows, "_projects"),
        "previous": previous,
        "best_hours": {"from": hours["from"], "to": hours["to"], "stretches": hours["stretches"]} if hours else None,
        "next": upcoming[:12],
    }  # fmt: skip


def path_of(vault: Vault, key: str):  # noqa: ANN201 - a Path
    return vault.root / "weeks" / f"{key}.json"


def inputs_stamp(vault: Vault, day: str) -> list:
    """The stamps of the files a week is made from (the two weeks' timelines and the reviews)."""
    _, monday = week_key(day)
    out = []
    for n in range(-7, 7):
        folder = vault.day_path((monday + timedelta(days=n)).isoformat(), must_exist=False)
        out += [file_stamp(stored(folder / name)) for name in ("timeline.jsonl", "review.json")]
    return out


def write(vault: Vault, store: LifeStore | None, day: str, zone: str, *, now: datetime) -> Record:
    """Write the week of ``day`` and its note when its numbers changed; returns ``written`` and the week."""
    week = collect(vault, store, day, zone)
    path = path_of(vault, week["week"])
    content = {k: v for k, v in week.items()}
    try:
        old = json.loads(read_text(path)) if path.exists() else None
    except (OSError, ValueError):
        old = None
    if old is not None and {k: v for k, v in old.items() if k != "made"} == content:
        return {"written": False, "week": old}
    week = {**content, "made": now.astimezone(ZoneInfo(zone)).isoformat(timespec="seconds")}
    contracts.validate("week", week)
    vault.write_json(path, week)
    if store is not None:
        from ..notes import Notes

        Notes(store).write_week(week)
    return {"written": True, "week": week}


def due(now: datetime, zone: str) -> str | None:
    """The day of the last week whose Sunday's review day has ended, once it has: a day of it."""
    current = timeline.review_day(now, ZoneInfo(zone))
    monday = week_key(current)[1]
    return (monday - timedelta(days=1)).isoformat()

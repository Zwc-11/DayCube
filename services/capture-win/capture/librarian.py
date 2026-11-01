"""Keeps the day's capture searchable, its linked notes current, its timeline and its review
written while the receiver runs.

Every ``every_s`` (10 minutes) it looks at today's capture logs (and yesterday's, until that day's
last changes are in), and when they changed it brings the day into the vault database and writes
its notes (``vault/notes``: the daily note, the pages that held attention, the courses). So
"what did I read about X" and the Obsidian folder follow the day without anyone running a
command. It uses its own ``Vault`` object, so it never holds the lock the receiver appends under,
and keyword search only: embedding the day's text is left to ``daycube index`` or the next search.

Then it writes the timeline (``vault/<date>/timeline.jsonl``, ``ingest/review/timeline.py``) of the
review day under way, which runs from 04:00 to 04:00: every round, since what is in front now is
still in front. The review day before is written again only when one of its files changed.

And the review (``vault/<date>/review.json``, ``ingest/review/summary.py``): a draft once after
23:00, the final in the first round after 04:00, when the review day has ended, and again
whenever that day's facts change (a phone import, a relabel). In its first round it writes the
final of each of the last ``CATCH_UP_DAYS`` days that has records and no final (the computer was
off at 04:00). Not at shutdown: Windows gives a program a few seconds. The same facts are never
written twice, so a model is asked once per day and change, not once per round.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("capture.librarian")

EVERY_S = 600.0
FIRST_S = 60.0  # after start, so a sign-in is not slowed by it
DRAFT_AT = time(23, 0)  # the evening's draft review
CATCH_UP_DAYS = 7  # at start, finals for this many days back that have none


class Librarian(threading.Thread):
    def __init__(
        self, vault_root: Path, zone: str, every_s: float = EVERY_S, labeler: Any = None, writer: Any = None,
        settings: Any = None,
    ) -> None:  # fmt: skip
        """``labeler``: the language model for apps and sites no category rule knows
        (``ingest.review.labels.llm_labeler``); ``writer``: the one that words the review
        (``ingest.integrations.models.language_model``). None keeps everything on this machine."""
        super().__init__(name="librarian", daemon=True)
        self.vault_root, self.zone, self.every_s = vault_root, zone, every_s
        self.labeler, self.writer = labeler, writer
        self.settings = settings  # ingest.config.Settings: the morning push (None: no push)
        self.weeks: dict[str, list] = {}  # week's day -> stamp of the files it was written from
        self.stopping = threading.Event()
        self.seen: dict[str, dict] = {}  # day -> capture stamp its notes were last written from
        # review day -> (stamp of the files its timeline was written from, whether it was under way)
        self.timelines: dict[str, tuple[dict, bool]] = {}
        self.report: dict[str, dict] = {}  # review day -> its timeline's last report
        self.caught_up = False

    def run(self) -> None:
        if self.stopping.wait(FIRST_S):
            return
        while True:
            steps = ((self.step, "notes"), (self.step_timeline, "timeline"), (self.step_review, "review"),
                     (self.step_week, "week"), (self.step_push, "morning push"))  # fmt: skip
            for step, what in steps:
                try:
                    step()
                except Exception:  # noqa: BLE001 - logged; the next round tries again
                    log.exception("the day's %s could not be written", what)
            if self.stopping.wait(self.every_s):
                return

    def step(self, now: datetime | None = None) -> list[str]:
        """Write the notes of today, and yesterday, when their capture changed; returns the days written."""
        from ingest.capture import capture_stamp
        from ingest.notes import Notes
        from ingest.store import LifeStore
        from ingest.vault import Vault

        now = now or datetime.now(ZoneInfo(self.zone))
        today = now.date()
        vault = Vault(self.vault_root)
        store = LifeStore(vault, zone=self.zone)
        written = []
        for day in ((today - timedelta(days=1)).isoformat(), today.isoformat()):
            stamp = capture_stamp(vault, day)
            if not any(key.startswith(f"capture/{day}/") for key in stamp) or self.seen.get(day) == stamp:
                continue
            paths = Notes(store).write_day(day)
            self.seen[day] = stamp
            written.append(day)
            log.info("notes for %s: %d written", day, len(paths))
        for day in [d for d in self.seen if d < (today - timedelta(days=1)).isoformat()]:
            del self.seen[day]
        return written

    def step_timeline(self, now: datetime | None = None) -> list[str]:
        """Write the timeline of the review day under way, and of the one before when its files
        changed; returns the review days written."""
        from ingest.review import has_inputs, inputs_stamp, review_day, write
        from ingest.vault import Vault

        now = now or datetime.now(ZoneInfo(self.zone))
        current = review_day(now, ZoneInfo(self.zone))
        before = (date.fromisoformat(current) - timedelta(days=1)).isoformat()
        vault = Vault(self.vault_root)
        written = []
        for day in (before, current):
            if not has_inputs(vault, day):
                continue
            stamp = inputs_stamp(vault, day)
            if day != current and self.timelines.get(day) == (stamp, False):
                continue
            # The day under way has what is in front now; a finished day is written once more as
            # recorded, then again only when one of its files changes.
            report = write(vault, day, self.zone, now=now if day == current else None, labeler=self.labeler)
            self.timelines[day], self.report[day] = (stamp, day == current), report
            if not report["written"]:
                continue  # nothing recorded in its hours
            written.append(day)
            log.info("timeline for %s: %d segments, %.1f h at the desk, %s other", day, report["segments"],
                     report["desk_s"] / 3600, "-" if report["other_share"] is None else f"{report['other_share']:.0%}")  # fmt: skip
            for error in report["errors"]:
                log.warning("timeline for %s: %s", day, error)
        for day in [d for d in self.timelines if d < before]:
            del self.timelines[day]
            self.report.pop(day, None)
        return written

    def step_review(self, now: datetime | None = None) -> list[tuple[str, str]]:
        """Write the reviews that are due (see the module's docstring); returns (review day, why)
        for each one written."""
        from ingest.notes import Notes
        from ingest.review import has_inputs, review_day
        from ingest.review.summary import read_review, write_review
        from ingest.store import LifeStore
        from ingest.vault import Vault

        zone = ZoneInfo(self.zone)
        now = now or datetime.now(zone)
        current = review_day(now, zone)
        today = date.fromisoformat(current)
        before = (today - timedelta(days=1)).isoformat()
        vault = Vault(self.vault_root)
        store = LifeStore(vault, zone=self.zone)
        due: list[tuple[str, str | None]] = []
        if not self.caught_up:
            for back in range(CATCH_UP_DAYS, 1, -1):
                day = (today - timedelta(days=back)).isoformat()
                review = read_review(vault, day)
                if (review is None or review["kind"] != "final") and has_inputs(vault, day):
                    due.append((day, "final"))
            self.caught_up = True
        if has_inputs(vault, before):
            due.append((before, "final"))  # written once, then only when its facts change
        if now >= datetime.combine(today, DRAFT_AT, zone) and has_inputs(vault, current):
            review = read_review(vault, current)
            if review is None or datetime.fromisoformat(review["made"]) < datetime.combine(today, DRAFT_AT, zone):
                due.append((current, "draft"))
        written = []
        for day, kind in due:
            out = write_review(vault, store, day, self.zone, now=now, model=self.writer, labeler=self.labeler, kind=kind)
            if not out["written"]:
                continue
            written.append((day, out["why"]))
            review = out["review"]
            log.info("review for %s (%s, %s): %s, by %s", day, review["kind"], out["why"], review["headline"],
                     review["writer"]["name"])  # fmt: skip
            for reason in review["writer"]["rejected"]:
                log.warning("review for %s: the model's answer was not used: %s", day, reason)
            if store.knows(day):
                with contextlib.suppress(FileNotFoundError):  # a day with records only after midnight
                    Notes(store).write_day(day)  # its Summary section
        return written

    def step_week(self, now: datetime | None = None) -> str | None:
        """Write the last finished week (Monday to Sunday) once Sunday's review day has ended, and
        again when one of its days changes; returns the week written."""
        from ingest.review import weekly
        from ingest.store import LifeStore
        from ingest.vault import Vault

        now = now or datetime.now(ZoneInfo(self.zone))
        day = weekly.due(now, self.zone)
        vault = Vault(self.vault_root)
        stamp = weekly.inputs_stamp(vault, day)
        if self.weeks.get(day) == stamp:
            return None
        if not any(stamp):
            self.weeks[day] = stamp
            return None
        out = weekly.write(vault, LifeStore(vault, zone=self.zone), day, self.zone, now=now)
        self.weeks = {day: stamp}
        if out["written"]:
            week = out["week"]
            log.info("week %s: %.1f h at the desk, %d stretches of 25 min or more", week["week"],
                     week["totals"]["desk_s"] / 3600, week["totals"]["focus"])  # fmt: skip
            return week["week"]
        return None

    def step_push(self, now: datetime | None = None) -> dict | None:
        """Send yesterday's review to the phone once, after the push time (off unless set)."""
        if self.settings is None or not getattr(self.settings, "summary_push", ""):
            return None
        from ingest.review import push
        from ingest.vault import Vault

        out = push.step(Vault(self.vault_root), self.settings, now or datetime.now(ZoneInfo(self.zone)), self.zone)
        if out is not None:
            if out.get("sent"):
                log.info("review for %s pushed by %s", out["date"], out["channel"])
            else:
                log.warning("review for %s not pushed (attempt %d): %s", out["date"], out["attempts"], out.get("error"))
        return out

    def stop(self) -> None:
        self.stopping.set()

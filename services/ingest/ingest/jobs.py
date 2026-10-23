"""Process-local index job ownership, duplicate suppression and bounded admission."""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from .vault import Record

logger = logging.getLogger(__name__)


class JobsFull(Exception):
    """No capacity remains for a distinct active job."""


@dataclass
class _Entry:
    key: tuple
    state: Record


class IndexJobs:
    """Own up to eight queued/running jobs and retain at most 100 total job records.

    Admission and status transitions share a lock; builders run outside it. An active job may
    be shared only by requests for the same day and source version. Completed/failed jobs never
    suppress a new rebuild, and only terminal records can be evicted.
    """

    def __init__(self, build: Callable[[str], Record], *, max_active: int = 8, max_entries: int = 100) -> None:
        if not 1 <= max_active <= max_entries:
            raise ValueError("Require 1 <= max_active <= max_entries")
        self._build = build
        self._max_active = max_active
        self._max_entries = max_entries
        self._entries: dict[str, _Entry] = {}
        self._active: dict[tuple, str] = {}
        self._lock = Lock()

    def submit(self, date: str, source_version: tuple) -> tuple[str, bool]:
        """Return the job id and whether the caller owns scheduling its background task."""
        key = (date, source_version)
        with self._lock:
            existing = self._active.get(key)
            if existing is not None:
                return existing, False
            if len(self._active) >= self._max_active:
                raise JobsFull
            if len(self._entries) >= self._max_entries:
                finished = next(
                    identifier
                    for identifier, entry in self._entries.items()
                    if entry.state["status"] in {"complete", "failed"}
                )
                del self._entries[finished]
            identifier = uuid4().hex
            state = {
                "id": identifier,
                "date": date,
                "status": "queued",
                "stage": "index",
                "pct": 0,
                "message": "Queued",
                "created_at": datetime.now(UTC).isoformat(),
            }
            self._entries[identifier] = _Entry(key, state)
            self._active[key] = identifier
            return identifier, True

    def snapshot(self, identifier: str) -> Record | None:
        """A detached, coherent state; callers cannot mutate registry-owned results."""
        with self._lock:
            entry = self._entries.get(identifier)
            return copy.deepcopy(entry.state) if entry is not None else None

    def run(self, identifier: str) -> None:
        with self._lock:
            entry = self._entries.get(identifier)
            if entry is None or entry.state["status"] != "queued":
                return
            entry.state.update(status="running", stage="index", pct=10)
            date = entry.state["date"]
        try:
            result = self._build(date)
        except Exception:
            logger.exception("Index job %s failed for %s", identifier, date)
            outcome = {"status": "failed", "message": "Index build failed; run daycube validate for diagnostics"}
        else:
            outcome = {"status": "complete", "pct": 100, "result": result, "message": "Local FTS5 index rebuilt"}
        with self._lock:
            entry.state.update(copy.deepcopy(outcome))
            del self._active[entry.key]

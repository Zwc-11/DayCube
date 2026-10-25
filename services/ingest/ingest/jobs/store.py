"""The vault's work queue (``vault/jobs.sqlite``): what each processing stage still has to do.

A job is one stage run on one input, for example ``splat_train`` on ``{"date": ..., "moment": ...}``.
Its key is the hash of the stage, the stage's version and the inputs, so adding the same job again
does nothing, and raising a stage's version makes its jobs new.

Workers lease a job for ``LEASE_S`` and extend the lease while they run. A lease that runs out
(the worker died) puts the job back, counted as an attempt. A failure is retried after 1, then 4
minutes, and is ``failed`` after ``MAX_ATTEMPTS``, with its error kept. Failed jobs wait for
``retry`` (``daycube jobs retry``).

The receiver, the importer and the command line open the same file. SQLite's write-ahead log on
the local disk lets them; every change is one short ``BEGIN IMMEDIATE`` transaction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

MAX_ATTEMPTS = 3
LEASE_S = 120.0
BACKOFF_S = 60.0  # then 4x per attempt

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    stage TEXT NOT NULL,
    version INTEGER NOT NULL,
    key TEXT NOT NULL UNIQUE,
    inputs TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    not_before REAL NOT NULL DEFAULT 0,
    lease_until REAL,
    started REAL,
    worker TEXT,
    error TEXT,
    outputs TEXT,
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs (status, stage, priority DESC, id);
"""


@dataclass(frozen=True)
class Job:
    id: int
    stage: str
    version: int
    inputs: dict
    attempts: int
    worker: str | None = None


def job_key(stage: str, version: int, inputs: dict) -> str:
    return hashlib.sha256(
        json.dumps([stage, version, inputs], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Jobs:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, timeout=30, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)

    @classmethod
    def of(cls, vault_root: Path) -> Jobs:
        return cls(vault_root / "jobs.sqlite")

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def add(
        self, stage: str, version: int, inputs: dict, *, priority: int = 0, now: float | None = None
    ) -> tuple[int, bool]:
        """The job's id, and whether it is new (False: this stage, version and input exist already)."""
        now = time.time() if now is None else now
        key = job_key(stage, version, inputs)
        with self._write() as db:
            cursor = db.execute(
                "INSERT INTO jobs (stage, version, key, inputs, priority, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (key) DO NOTHING",
                (stage, version, key, json.dumps(inputs, sort_keys=True), priority, now, now),
            )
            new = cursor.rowcount == 1
            (job_id,) = db.execute("SELECT id FROM jobs WHERE key = ?", (key,)).fetchone()
        return int(job_id), new

    def lease(
        self, worker: str, stages: list[str], *, lease_s: float = LEASE_S, now: float | None = None
    ) -> Job | None:
        """The next job of one of ``stages`` (highest priority, then oldest), leased to ``worker``."""
        now = time.time() if now is None else now
        if not stages:
            return None
        with self._write() as db:
            self._expire(db, now)
            marks = ",".join("?" * len(stages))
            row = db.execute(
                f"SELECT * FROM jobs WHERE status = 'queued' AND stage IN ({marks}) AND not_before <= ?"
                " ORDER BY priority DESC, id LIMIT 1",
                (*stages, now),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET status = 'leased', worker = ?, lease_until = ?, started = ?, updated = ? WHERE id = ?",
                (worker, now + lease_s, now, now, row["id"]),
            )
        return Job(row["id"], row["stage"], row["version"], json.loads(row["inputs"]), row["attempts"], worker)

    def _expire(self, db: sqlite3.Connection, now: float) -> None:
        for row in db.execute(
            "SELECT id, worker, attempts FROM jobs WHERE status = 'leased' AND lease_until < ?", (now,)
        ).fetchall():
            attempts = row["attempts"] + 1
            db.execute(
                "UPDATE jobs SET status = ?, attempts = ?, worker = NULL, lease_until = NULL, error = ?, updated = ? WHERE id = ?",
                (
                    "queued" if attempts < MAX_ATTEMPTS else "failed",
                    attempts,
                    f"{row['worker']} stopped answering",
                    now,
                    row["id"],
                ),
            )

    def heartbeat(self, job_id: int, worker: str, *, lease_s: float = LEASE_S, now: float | None = None) -> bool:
        """Extend the lease; False if the job is no longer this worker's (it ran out and was re-queued)."""
        now = time.time() if now is None else now
        with self._write() as db:
            cursor = db.execute(
                "UPDATE jobs SET lease_until = ?, updated = ? WHERE id = ? AND worker = ? AND status = 'leased'",
                (now + lease_s, now, job_id, worker),
            )
        return cursor.rowcount == 1

    def done(self, job_id: int, worker: str, outputs: dict, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._write() as db:
            cursor = db.execute(
                "UPDATE jobs SET status = 'done', outputs = ?, error = NULL, lease_until = NULL, updated = ?"
                " WHERE id = ? AND worker = ? AND status = 'leased'",
                (json.dumps(outputs, sort_keys=True), now, job_id, worker),
            )
        return cursor.rowcount == 1

    def fail(
        self,
        job_id: int,
        worker: str,
        error: str,
        *,
        retry: bool = True,
        max_attempts: int = MAX_ATTEMPTS,
        now: float | None = None,
    ) -> str | None:
        """Record a failure; returns the job's new status (``queued`` to try again later, or ``failed``)."""
        now = time.time() if now is None else now
        with self._write() as db:
            row = db.execute(
                "SELECT attempts FROM jobs WHERE id = ? AND worker = ? AND status = 'leased'", (job_id, worker)
            ).fetchone()
            if row is None:
                return None
            attempts = row["attempts"] + 1
            status = "queued" if retry and attempts < max_attempts else "failed"
            db.execute(
                "UPDATE jobs SET status = ?, attempts = ?, not_before = ?, error = ?, worker = NULL, lease_until = NULL, updated = ?"
                " WHERE id = ?",
                (status, attempts, now + BACKOFF_S * 4 ** (attempts - 1), error[-4000:], now, job_id),
            )
        return status

    def retry(self, *, stage: str | None = None, job_id: int | None = None, now: float | None = None) -> int:
        """Failed jobs (of one stage, or one job) queued again from their first attempt."""
        now = time.time() if now is None else now
        where, args = "status = 'failed'", []
        if stage is not None:
            where, args = where + " AND stage = ?", [stage]
        if job_id is not None:
            where, args = where + " AND id = ?", [*args, job_id]
        with self._write() as db:
            cursor = db.execute(
                f"UPDATE jobs SET status = 'queued', attempts = 0, not_before = 0, updated = ? WHERE {where}",
                (now, *args),
            )
        return cursor.rowcount

    def get(self, job_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row(row) if row else None

    def find(self, stage: str, inputs: dict) -> list[dict]:
        """Every version's job of ``stage`` on ``inputs``, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE stage = ? AND inputs = ? ORDER BY id DESC",
                (stage, json.dumps(inputs, sort_keys=True)),
            ).fetchall()
        return [_row(r) for r in rows]

    def status(self, *, now: float | None = None) -> dict:
        """Counts per stage, what runs, and the latest failures."""
        now = time.time() if now is None else now
        with self._lock:
            counts = self._db.execute("SELECT stage, status, COUNT(*) AS n FROM jobs GROUP BY stage, status").fetchall()
            running = self._db.execute("SELECT * FROM jobs WHERE status = 'leased' ORDER BY started").fetchall()
            failed = self._db.execute(
                "SELECT * FROM jobs WHERE status = 'failed' ORDER BY updated DESC LIMIT 10"
            ).fetchall()
            oldest = self._db.execute("SELECT MIN(created) FROM jobs WHERE status = 'queued'").fetchone()[0]
        stages: dict[str, dict[str, int]] = {}
        for row in counts:
            stages.setdefault(row["stage"], {"queued": 0, "leased": 0, "done": 0, "failed": 0})[row["status"]] = row[
                "n"
            ]
        return {
            "stages": stages,
            "running": [_row(r) for r in running],
            "failed": [_row(r) for r in failed],
            "oldest_queued_s": None if oldest is None else round(now - oldest, 1),
        }


def _row(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["inputs"] = json.loads(out["inputs"])
    out["outputs"] = json.loads(out["outputs"]) if out["outputs"] else None
    out.pop("key", None)
    return out

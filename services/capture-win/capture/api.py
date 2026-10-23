"""The capture receiver on 127.0.0.1:7700: the one place browser records and gaze meet.

Writes append-only logs to ``vault/capture/<local date>/``:

- ``browser.jsonl``: pages, focus, titles, idle state, searches, clicks, selections, media
- ``blocks.jsonl``: snapshots of the text on screen and where the viewport sits
- ``gaze.jsonl``: fixations, each with the page point and text block it landed on
- ``attention.jsonl``: dwells (see ``attention.py``), a start and an end record per dwell

A pause is remembered in ``vault/capture/paused`` until resumed, so a restart never undoes it.
"""

from __future__ import annotations

import json
import logging
import secrets
import subprocess
import sys
import threading
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ingest.config import Settings
from ingest.vault import Vault
from pydantic import TypeAdapter, ValidationError

from .attention import Attention
from .records import Blocks, BrowserRecord, GazeBatch, View

log = logging.getLogger("capture")

BROWSER = TypeAdapter(BrowserRecord)
MAX_BATCH = 500
# The extension queues while the receiver is down (Opera can start first, or the receiver can
# be stopped for hours). Older records are refused so a client cannot write arbitrary days.
MAX_AGE = timedelta(hours=24)
MAX_AHEAD = timedelta(minutes=5)
SEEN_IDS = 200_000
# Fixations arrive about a second after they end, so a run is closed only after this extra wait.
GAZE_LATENCY = timedelta(seconds=3)
GAZE_SILENT = timedelta(seconds=30)  # the tracker reports every second while it runs


class Receiver:
    def __init__(
        self, settings: Settings, *, receive: bool, attention: Attention | None, on_dwell: list[str] | None
    ) -> None:
        self.vault = Vault(settings.vault_root)
        self.vault.initialize()
        self.zone = ZoneInfo(settings.timezone)
        self.marker = self.vault.root / "capture" / "paused"
        self.paused = not receive or self.marker.exists()
        self.lock = threading.Lock()
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.signals: list[dict] = []
        self.gaze: dict | None = None
        self.last: dict[str, str] = {}
        self.since = datetime.now(UTC).isoformat()
        self.attention = attention or Attention()
        self.on_dwell = on_dwell
        self.hook: subprocess.Popen | None = None

    # -- state -------------------------------------------------------------------------------------

    def status(self) -> dict:
        a = self.attention
        gaze = self.gaze or {"tracking": False, "reason": "not running"}
        if self.gaze and datetime.now(UTC) - datetime.fromisoformat(self.gaze["at"]) > GAZE_SILENT:
            gaze = {**self.gaze, "tracking": False, "reason": "no reports"}
        return {
            "paused": self.paused,
            "since": self.since,
            "browser": {"last": self.last.get("browser")},
            "gaze": {"last": self.last.get("gaze"), **gaze},
            "dwell": {"threshold_s": a.threshold_s, "factor": a.factor, "wpm": a.wpm},
            "capabilities": ["browser", "gaze", "attention"],
        }

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            if paused and not self.paused:
                self._write({}, self.attention.end())
                self.attention.front = None
            self.paused = paused
            self.marker.parent.mkdir(parents=True, exist_ok=True)
            if paused:
                self.marker.touch()
            else:
                self.marker.unlink(missing_ok=True)

    def _fresh(self, record_id: str) -> bool:
        if record_id in self.seen:
            return False
        self.seen[record_id] = None
        if len(self.seen) > SEEN_IDS:
            self.seen.popitem(last=False)
        return True

    def _in_window(self, t: datetime, now: datetime) -> bool:
        return now - MAX_AGE <= t <= now + MAX_AHEAD

    def _day(self, t: datetime | str) -> str:
        moment = datetime.fromisoformat(t) if isinstance(t, str) else t
        return moment.astimezone(self.zone).date().isoformat()

    def _write(self, lines: dict[tuple[str, str], list[dict]], dwells: list[dict]) -> None:
        for event in dwells:
            lines.setdefault((self._day(event["t"]), "attention.jsonl"), []).append(event)
            if event["phase"] == "start":
                self._signal(event)
        for (day, name), records in lines.items():
            # Appending costs the batch, not the day: rewriting the log made capture O(n^2).
            self.vault.append_lines(self.vault.root / "capture" / day / name, records)

    # -- dwell actions -----------------------------------------------------------------------------

    def _signal(self, event: dict) -> None:
        """Queue a dwell for the extension (delivered with its next response) and run the hook."""
        keep = ("tab", "doc", "block", "dwell_s", "reading_s", "url", "title")
        self.signals = [
            *self.signals[-19:],
            {"kind": "dwell", **{k: event[k] for k in keep}, "text": (event["text"] or "")[:300]},
        ]
        if not self.on_dwell or (self.hook is not None and self.hook.poll() is None):
            return  # no hook, or the previous run is still going
        try:
            flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
            self.hook = subprocess.Popen(
                self.on_dwell, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags,
            )  # fmt: skip
        except OSError as error:
            log.warning("dwell hook failed to start: %s", error)
            return
        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        threading.Thread(target=_feed, args=(self.hook, payload), daemon=True).start()

    # -- intake ------------------------------------------------------------------------------------

    def browser(self, items: list[dict[str, Any]]) -> dict:
        now = datetime.now(UTC)
        accepted = duplicates = 0
        rejected: list[dict] = []
        lines: dict[tuple[str, str], list[dict]] = {}
        dwells: list[dict] = []
        with self.lock:
            if self.paused:
                raise HTTPException(409, "Receiver is paused")
            for item in items:
                try:
                    record = BROWSER.validate_python(item)
                except ValidationError as error:
                    rejected.append({"id": str(item.get("id", ""))[:64], "reason": _reason(error)})
                    continue
                if not self._in_window(record.t, now):
                    rejected.append({"id": record.id, "reason": "older than a day or in the future"})
                    continue
                if not self._fresh(record.id):
                    duplicates += 1
                    continue
                accepted += 1
                dwells += self.attention.browser(record)
                if isinstance(record, View):
                    continue  # live only
                name = "blocks.jsonl" if isinstance(record, Blocks) else "browser.jsonl"
                lines.setdefault((self._day(record.t), name), []).append(record.model_dump(mode="json"))
            self._write(lines, dwells)
            if accepted:
                self.last["browser"] = now.isoformat()
            signals, self.signals = self.signals, []
        return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected, "signals": signals}

    def gaze_batch(self, batch: GazeBatch) -> dict:
        now = datetime.now(UTC)
        accepted = duplicates = stale = 0
        lines: dict[tuple[str, str], list[dict]] = {}
        dwells: list[dict] = []
        with self.lock:
            if self.paused:
                raise HTTPException(409, "Receiver is paused")
            self.gaze = {**batch.state.model_dump(mode="json"), "at": now.isoformat()}
            self.attention.gaze_monitor(batch.state.monitor)
            for fixation in sorted(batch.fixations, key=lambda f: f.t):
                if not self._in_window(fixation.t, now):
                    stale += 1
                    continue
                if not self._fresh(fixation.id):
                    duplicates += 1
                    continue
                accepted += 1
                hit, events = self.attention.fixation(fixation)
                dwells += events
                line = fixation.model_dump(mode="json")
                if hit is not None:
                    line |= {"tab": hit.tab, "doc": hit.doc, "page": [hit.x, hit.y], "block": hit.block}
                lines.setdefault((self._day(fixation.t), "gaze.jsonl"), []).append(line)
            dwells += self.attention.advance(now - GAZE_LATENCY)
            self._write(lines, dwells)
            if accepted:
                self.last["gaze"] = now.isoformat()
        return {"accepted": accepted, "duplicates": duplicates, "stale": stale}


def _feed(process: subprocess.Popen, payload: bytes) -> None:
    try:
        process.stdin.write(payload)
        process.stdin.close()
    except OSError:
        pass


def _reason(error: ValidationError) -> str:
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    return f"{where}: {first['msg']}"[:200]


def create_capture_app(
    settings: Settings | None = None,
    *,
    receive: bool = False,
    attention: Attention | None = None,
    on_dwell: list[str] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    receiver = Receiver(settings, receive=receive, attention=attention, on_dwell=on_dwell)
    vault = receiver.vault
    app = FastAPI(title="DAYCUBE capture receiver", version="0.2.0")
    app.state.receiver = receiver
    bearer = HTTPBearer(auto_error=False)
    origins = [f"chrome-extension://{settings.extension_id}"] if settings.extension_id else []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if credentials is None or not secrets.compare_digest(credentials.credentials, vault.token):
            raise HTTPException(401, "Valid vault bearer token required")

    @app.get("/status")
    def status() -> dict:
        return receiver.status()

    @app.post("/pause", dependencies=[Depends(authorize)])
    def pause() -> dict:
        receiver.set_paused(True)
        return receiver.status()

    @app.post("/resume", dependencies=[Depends(authorize)])
    def resume() -> dict:
        receiver.set_paused(False)
        return receiver.status()

    @app.post("/events", dependencies=[Depends(authorize)])
    def events(records: list[dict[str, Any]] = Body(...)) -> dict:
        """Browser records. Invalid ones are returned in ``rejected`` instead of failing the batch,
        so one bad record cannot wedge the extension's queue."""
        if len(records) > MAX_BATCH:
            raise HTTPException(413, f"At most {MAX_BATCH} records per batch")
        return receiver.browser(records)

    @app.post("/gaze", dependencies=[Depends(authorize)])
    def gaze(batch: GazeBatch) -> dict:
        return receiver.gaze_batch(batch)

    return app

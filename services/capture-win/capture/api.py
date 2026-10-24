"""The capture receiver on 127.0.0.1:7700: the one place browser records and gaze meet.

Writes append-only logs to ``vault/capture/<local date>/``:

- ``browser.jsonl``: pages, focus, titles, idle state, searches, clicks, selections, media
- ``blocks.jsonl``: snapshots of the text on screen and where the viewport sits
- ``gaze.jsonl``: fixations, each with the page point and text block it landed on
- ``attention.jsonl``: dwells (see ``attention.py``), a start and an end record per dwell
- ``reading.jsonl``: what was read on each block the eyes went through, line by line (see
  ``reading.py``); ``gaze.jsonl`` has the line decoded for each fixation, ``/status`` the current one
- ``health.jsonl``: every 10 minutes while gaze arrives, the tracker's state (frame rate, face,
  how much gaze became fixations, noise) and the live accuracy measured from clicks, so a session
  can be judged afterwards without a test
- ``windows.jsonl``: the app in front, each time it changes, and when the person is away (from the
  screen reader, ``screen.py``, which also turns the window under the gaze into ``blocks`` records
  like a browser page's, so reading, dwells and lines work in every app)

A pause is remembered in ``vault/capture/paused`` until resumed, so a restart never undoes it,
and every pause and resume is logged in ``vault/capture/pauses.jsonl``. The extension's excluded
domains are mirrored to ``vault/capture/exclusions.json``. The history import uses both, so it
cannot bring back what was paused or excluded.
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from ingest.config import Settings
from ingest.vault import Vault, read_lines
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from .attention import Attention
from .reading import LineBox
from .records import Blocks, BrowserRecord, GazeBatch, LivePoint, View
from .restart import code_stamp

log = logging.getLogger("capture")

BROWSER = TypeAdapter(BrowserRecord)
MAX_BATCH = 500
# The extension queues while the receiver is down (Opera can start first, or the receiver can
# be stopped for hours). Older records are refused so a client cannot write arbitrary days.
MAX_AGE = timedelta(hours=24)
MAX_AHEAD = timedelta(minutes=5)
SEEN_IDS = 200_000
WARM_TEXT_BYTES = 64 << 20  # block texts are reloaded from logs up to this size after a restart
# Fixations arrive about a second after they end, so a run is closed only after this extra wait.
GAZE_LATENCY = timedelta(seconds=3)
LIVE_WATCH_S = 10.0  # the tracker streams live gaze while something asked for it this recently
GAZE_SILENT = timedelta(seconds=30)
NATIVE_FRESH = timedelta(
    seconds=5
)  # the tracker reports every second: older, pages draw the ring again  # the tracker reports every second while it runs
HEALTH_EVERY = timedelta(minutes=10)
CODE = code_stamp()  # the code this receiver loaded; `capture restart` and doctor compare it with the files


class Display(BaseModel):
    gaze_ring: bool


class Exclusions(BaseModel):
    domains: list[str] = Field(max_length=2000)


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
        self.health_at: datetime | None = None
        self.live: tuple[float, float, float] | None = None  # (epoch t, x, y): the gaze point now, never stored
        self.last_fixation: tuple[float, float, float] | None = None  # (epoch end, x, y), for the screen reader
        # The screen reader (screen.py), started by `python -m capture run`; why not, when it is not.
        self.screen: Any = None
        self.screen_problem: str | None = "not started"
        self.watched_at = -1e9  # when the extension last asked for the live point
        # The gaze ring's path, for doctor: when live points arrived, when the page asked, and
        # whether the answer carried a live point (or fell back to the last fixation).
        self.live_points: deque[float] = deque(maxlen=1000)
        self.live_asks: deque[tuple[float, bool]] = deque(maxlen=1000)
        # Whether the tracker draws the gaze ring over every window (the extension's "Show where my
        # eyes are" option, or `python -m capture ring on|off`), remembered across restarts.
        self.display_file = self.vault.root / "capture" / "display.json"
        self.ring = self._read_display().get("gaze_ring", False)
        self._warm_texts()

    def _warm_texts(self) -> None:
        """Reload block texts and lines from today's and yesterday's snapshots: the extension sends
        a block's text and lines once per page load, so pages open across a restart still have them
        for dwells and line decoding."""
        today = datetime.now(self.zone).date()
        for day in (today - timedelta(days=1), today):
            path = self.vault.root / "capture" / day.isoformat() / "blocks.jsonl"
            if not path.is_file() or path.stat().st_size > WARM_TEXT_BYTES:
                continue
            try:
                records = read_lines(path)
            except (OSError, ValueError) as error:
                log.warning("could not reload block texts from %s: %s", path, error)
                continue
            for record in records:
                for block in record.get("blocks", []):
                    if block.get("text") is not None:
                        self.attention.remember(
                            record["doc"], block["id"], block["tag"], block["text"], block.get("words")
                        )
                    if block.get("lines"):
                        with contextlib.suppress(TypeError, ValueError, KeyError):
                            lines = [LineBox(*line["rect"], line.get("text", "")) for line in block["lines"]]
                            self.attention.layout(record["doc"], block["id"], lines)

    def _read_display(self) -> dict:
        try:
            value = json.loads(self.display_file.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def set_ring(self, on: bool) -> None:
        with self.lock:
            self.ring = on
            self.display_file.parent.mkdir(parents=True, exist_ok=True)
            self.display_file.write_text(json.dumps({**self._read_display(), "gaze_ring": on}) + "\n", encoding="utf-8")

    def native_ring(self) -> bool:
        """Whether the tracker is drawing the ring over every window now, so pages do not draw theirs:
        switched on, not paused, and the tracker said so within the last few seconds."""
        gaze = self.gaze or {}
        overlay = gaze.get("overlay") or {}
        if not (self.ring and not self.paused and overlay.get("running") and gaze.get("at")):
            return False
        return datetime.now(UTC) - datetime.fromisoformat(gaze["at"]) <= NATIVE_FRESH

    def offset_grid(self, now: float, cols: int = 9, rows: int = 5) -> dict | None:
        """The click correction (attention.anchors) sampled on a grid over the monitor, for the
        tracker to correct the ring it draws as the receiver corrects fixations: offsets in monitor
        fractions at (i / (cols - 1), j / (rows - 1)), row by row. None without clicks to learn from."""
        anchors = self.attention.anchors
        if not anchors.anchors or anchors.monitor is None:
            return None
        dx, dy = [], []
        for j in range(rows):
            for i in range(cols):
                ox, oy = anchors.offset(i / (cols - 1), j / (rows - 1), now)
                dx.append(round(ox, 5))
                dy.append(round(oy, 5))
        return {"cols": cols, "rows": rows, "dx": dx, "dy": dy}

    # -- state -------------------------------------------------------------------------------------

    def status(self) -> dict:
        a = self.attention
        with self.lock:  # the click calibration is updated by gaze batches on other threads
            accuracy = a.health(datetime.now(UTC).timestamp())
            reading = a.reading
            decoding = a.lines_health()
            point = a.gaze_point
        if reading and datetime.now(UTC) - datetime.fromisoformat(reading["t"]) > timedelta(seconds=10):
            reading = None  # the line being read now, not the last one ever read
        if point and datetime.now(UTC) - datetime.fromisoformat(point["t"]) > timedelta(seconds=10):
            point = None
        gaze = self.gaze or {"tracking": False, "reason": "not running"}
        if self.gaze and datetime.now(UTC) - datetime.fromisoformat(self.gaze["at"]) > GAZE_SILENT:
            gaze = {**self.gaze, "tracking": False, "reason": "no reports"}
        if self.paused:
            # /gaze rejects paused batches. Do not present the last active report as live.
            gaze = {**gaze, "tracking": False, "reason": "paused", "fps": None, "face": None}
        return {
            "paused": self.paused,
            "since": self.since,
            "code": CODE,
            "browser": {"last": self.last.get("browser")},
            "gaze": {"last": self.last.get("gaze"), **gaze},
            "dwell": {"threshold_s": a.threshold_s, "factor": a.factor, "wpm": a.wpm},
            "accuracy": accuracy,
            "reading": reading,
            "line_decoding": decoding,
            "gaze_point": point,  # the last fixation placed on a page: viewport CSS px, with that page's scroll
            "ring": self.ring_health(),
            "display": {"gaze_ring": self.ring, "native": self.native_ring()},
            "screen": self.screen_state(),
            "capabilities": ["browser", "gaze", "attention"] + (["screen"] if self.screen is not None else []),
        }

    def screen_state(self) -> dict:
        if self.screen is None:
            return {"running": False, "reason": self.screen_problem}
        try:
            return self.screen.state()
        except Exception as error:  # noqa: BLE001 - status must answer whatever the reader's state
            return {"running": False, "reason": f"{type(error).__name__}: {error}"[:200]}

    def ring_health(self, window_s: float = 10.0) -> dict:
        """The gaze ring's path over the last ``window_s``: live points from the tracker per second,
        page requests per second (an extension older than 0.7.0 never asks), and the share of
        requests answered with a live point rather than the last fixation."""
        now = time.time()
        points = sum(1 for t in self.live_points if now - t <= window_s)
        asks = [live for t, live in self.live_asks if now - t <= window_s]
        return {
            "points_per_s": round(points / window_s, 1),
            "asks_per_s": round(len(asks) / window_s, 1),
            "live_share": round(sum(asks) / len(asks), 2) if asks else None,
            "ever_asked": bool(self.live_asks),
        }

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            changed = paused != self.paused
            ending = self.attention.clear() if paused and changed else []
            if paused:
                self.attention.clear()  # nothing on screen is placed while paused
            self.paused = paused  # the pause holds even if a write below fails
            if changed:
                self.gaze = None  # a resume needs a fresh tracker report, not pre-pause telemetry
            self.marker.parent.mkdir(parents=True, exist_ok=True)
            if paused:
                self.marker.touch()
            else:
                self.marker.unlink(missing_ok=True)
            if changed:
                record = {"t": datetime.now(UTC).astimezone(self.zone).isoformat(), "paused": paused}
                self.vault.append_lines(self.marker.parent / "pauses.jsonl", [record])
                if paused and self.screen is not None:  # the app in front stops counting
                    away = {"id": uuid.uuid4().hex, "t": record["t"], "kind": "away", "why": "paused"}
                    self.vault.append_lines(self.marker.parent / self._day(record["t"]) / "windows.jsonl", [away])
            self._write({}, ending)

    def set_exclusions(self, exclusions: Exclusions) -> None:
        with self.lock:
            domains = sorted({d.strip().lower() for d in exclusions.domains if d.strip()})
            self.vault.write_json(self.vault.root / "capture" / "exclusions.json", {"domains": domains})

    def _remember_ids(self, ids: dict[str, None]) -> None:
        """Called only after the records were written, so a failed write is retried, not dropped."""
        for record_id in ids:
            self.seen[record_id] = None
        while len(self.seen) > SEEN_IDS:
            self.seen.popitem(last=False)

    def _in_window(self, t: datetime, now: datetime) -> bool:
        return now - MAX_AGE <= t <= now + MAX_AHEAD

    def _day(self, t: datetime | str) -> str:
        moment = datetime.fromisoformat(t) if isinstance(t, str) else t
        return moment.astimezone(self.zone).date().isoformat()

    def _write(self, lines: dict[tuple[str, str], list[dict]], dwells: list[dict]) -> None:
        """``dwells``: what attention returned, dwell start/end records and line-by-line reads."""
        for event in dwells:
            name = "reading.jsonl" if event["kind"] == "read" else "attention.jsonl"
            lines.setdefault((self._day(event["t"]), name), []).append(event)
        for (day, name), records in lines.items():
            # Appending costs the batch, not the day: rewriting the log made capture O(n^2).
            self.vault.append_lines(self.vault.root / "capture" / day / name, records)
        for event in dwells:
            if event["kind"] == "dwell" and event["phase"] == "start":
                self._signal(event)

    # -- dwell actions -----------------------------------------------------------------------------

    def _signal(self, event: dict) -> None:
        """Queue a dwell for the extension (delivered with its next response) and run the hook."""
        keep = ("tab", "doc", "block", "dwell_s", "reading_s", "url", "title")
        if not str(event.get("doc", "")).startswith("scr-"):  # the extension can show it on its page
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
        return self._intake(items, screen=False)

    def screen_records(self, items: list[dict[str, Any]]) -> int:
        """Records from the screen reader (``blocks`` and ``view`` of the window under the gaze), taken
        like the extension's but without handing it the dwell signals queued for the extension.
        Returns how many were accepted; raises HTTPException(409) while paused."""
        return self._intake(items, screen=True)["accepted"]

    def app_window(self, record: dict) -> None:
        """The app in front changed, or the person went away (the screen reader): ``windows.jsonl``."""
        with self.lock:
            if self.paused:
                return
            self.vault.append_lines(self.vault.root / "capture" / self._day(record["t"]) / "windows.jsonl", [record])
            self.last["screen"] = datetime.now(UTC).isoformat()

    def gaze_now(self, fresh_s: float) -> tuple[float, float] | None:
        """Where the eyes are now, in fractions of the tracker's monitor, corrected by clicks like
        every fixation: the live point or the last fixation, whichever is newer, if at most
        ``fresh_s`` old."""
        now = time.time()
        with self.lock:
            if self.paused:
                return None
            newest = max((g for g in (self.live, self.last_fixation) if g is not None), default=None)
            if newest is None or now - newest[0] > fresh_s:
                return None
            t, x, y = newest
            dx, dy = self.attention.anchors.offset(x, y, t)
        return x - dx, y - dy

    def _intake(self, items: list[dict[str, Any]], *, screen: bool) -> dict:
        now = datetime.now(UTC)
        accepted = duplicates = 0
        rejected: list[dict] = []
        lines: dict[tuple[str, str], list[dict]] = {}
        dwells: list[dict] = []
        ids: dict[str, None] = {}  # this batch's new ids, in order
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
                if record.id in self.seen or record.id in ids:
                    duplicates += 1
                    continue
                accepted += 1
                ids[record.id] = None
                dwells += self.attention.browser(record)
                if isinstance(record, View):
                    continue  # live only
                name = "blocks.jsonl" if isinstance(record, Blocks) else "browser.jsonl"
                lines.setdefault((self._day(record.t), name), []).append(record.model_dump(mode="json"))
            self._write(lines, dwells)
            self._remember_ids(ids)
            if accepted:
                self.last["screen" if screen else "browser"] = now.isoformat()
            signals: list[dict] = []
            if not screen:
                signals, self.signals = self.signals, []
        return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected, "signals": signals}

    def gaze_batch(self, batch: GazeBatch) -> dict:
        now = datetime.now(UTC)
        accepted = duplicates = stale = 0
        lines: dict[tuple[str, str], list[dict]] = {}
        dwells: list[dict] = []
        ids: dict[str, None] = {}  # this batch's new ids, in order
        with self.lock:
            if self.paused:
                raise HTTPException(409, "Receiver is paused")
            self.gaze = {**batch.state.model_dump(mode="json"), "at": now.isoformat()}
            self.attention.gaze_state(batch.state)
            for fixation in sorted(batch.fixations, key=lambda f: f.t):
                if not self._in_window(fixation.t, now):
                    stale += 1
                    continue
                if fixation.id in self.seen or fixation.id in ids:
                    duplicates += 1
                    continue
                accepted += 1
                ids[fixation.id] = None
                end = fixation.t_end.timestamp()
                if self.last_fixation is None or end >= self.last_fixation[0]:
                    self.last_fixation = (end, fixation.x, fixation.y)
                hit, events = self.attention.fixation(fixation)
                dwells += events
                line = fixation.model_dump(mode="json")
                if hit is not None:
                    line |= {"tab": hit.tab, "doc": hit.doc, "page": [hit.x, hit.y], "block": hit.block, "p": hit.p}
                    if hit.line is not None:
                        line |= {"line": {"block": hit.line_block, "line": hit.line, "p": hit.line_p}}
                lines.setdefault((self._day(fixation.t), "gaze.jsonl"), []).append(line)
            dwells += self.attention.advance(now - GAZE_LATENCY)
            settled = batch.state.tracking and batch.state.coverage is not None  # a few seconds of gaze
            if settled and (self.health_at is None or now - self.health_at >= HEALTH_EVERY):
                self.health_at = now
                health = {"t": now.astimezone(self.zone).isoformat(), "gaze": batch.state.model_dump(mode="json")}
                health["accuracy"] = self.attention.health(now.timestamp())
                lines.setdefault((self._day(now), "health.jsonl"), []).append(health)
            self._write(lines, dwells)
            self._remember_ids(ids)
            if accepted:
                self.last["gaze"] = now.isoformat()
        # "live": a page shows the gaze ring, so the tracker streams the point to it now; not needed
        # while the tracker draws the ring itself ("ring"), with the click correction as a grid.
        native = self.native_ring()
        live = time.time() - self.watched_at < LIVE_WATCH_S and not native
        with self.lock:
            grid = self.offset_grid(now.timestamp()) if self.ring else None
            # Lines confirmed by reading order: labels for the tracker's learning (capture/labels.py).
            read = self.attention.labels.drain()
            line = self.attention.line_now if self.ring else None
        return {
            "accepted": accepted, "duplicates": duplicates, "stale": stale, "live": live,
            "ring": self.ring and not self.paused, "offset_grid": grid, "lines": read, "reading": line,
        }  # fmt: skip

    def live_point(self, point: LivePoint) -> None:
        if self.paused:
            raise HTTPException(409, "Receiver is paused")
        self.live = (point.t.timestamp(), point.x, point.y)
        self.live_points.append(time.time())

    def live_view(self, doc: str) -> dict:
        """For the gaze ring and line marker on page ``doc``: the live gaze point on it (viewport
        CSS px, at most 2 s old), the line being read, and the last fixation placed (for a tracker
        that does not stream live points)."""
        now = time.time()
        self.watched_at = now
        if self.native_ring():  # drawn over every window by the tracker: the page draws no ring
            with self.lock:
                reading = self.attention.reading
            if reading and (
                reading.get("doc", doc) != doc or now - datetime.fromisoformat(reading["t"]).timestamp() > 10
            ):
                reading = None
            return {"point": None, "reading": reading, "fixation": None, "native": True}
        with self.lock:
            live, point = self.live, None
            if live is not None and now - live[0] <= 2.0 and not self.paused:
                point = self.attention.live_point(doc, *live)
                if point is not None:
                    point["t"] = datetime.fromtimestamp(live[0], UTC).isoformat()
            reading, fixation = self.attention.reading, self.attention.gaze_point
        self.live_asks.append((now, point is not None))

        def fresh(item: dict | None) -> dict | None:
            if not item or item.get("doc", doc) != doc:
                return None
            return item if now - datetime.fromisoformat(item["t"]).timestamp() <= 10 else None

        return {"point": point, "reading": fresh(reading), "fixation": fresh(fixation)}


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

    @app.post("/exclusions", dependencies=[Depends(authorize)])
    def exclusions(body: Exclusions) -> dict:
        """The extension's excluded domains, so the history import leaves them out too."""
        receiver.set_exclusions(body)
        return {"domains": len(body.domains)}

    @app.get("/display")
    def display() -> dict:
        return {"gaze_ring": receiver.ring, "native": receiver.native_ring()}

    @app.post("/display", dependencies=[Depends(authorize)])
    def set_display(body: Display) -> dict:
        """Show or hide the gaze ring (the extension's "Show where my eyes are" option)."""
        receiver.set_ring(body.gaze_ring)
        return {"gaze_ring": receiver.ring, "native": receiver.native_ring()}

    @app.post("/gaze", dependencies=[Depends(authorize)])
    def gaze(batch: GazeBatch) -> dict:
        return receiver.gaze_batch(batch)

    @app.post("/gaze/live", dependencies=[Depends(authorize)])
    def gaze_live(point: LivePoint) -> dict:
        receiver.live_point(point)
        return {"ok": True}

    @app.get("/live", dependencies=[Depends(authorize)])
    def live(doc: str) -> dict:
        return receiver.live_view(doc)

    return app

"""Talking to the capture receiver on 127.0.0.1:7700: fixations and tracker state, once a second
(sooner when a fixation completes), and the live gaze point every frame while the receiver says
something shows it (the extension's gaze ring). Live points are not stored anywhere.

Live points go out from their own thread over one kept-alive connection, so the frame loop never
waits on the network (a POST that opens a connection costs 1-3 ms on Windows, at 30 a second);
a point not yet sent when the next arrives is replaced by it."""

from __future__ import annotations

import json
import math
import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .fixations import Fixation

BATCH = 1000
QUEUE = 20_000  # about 90 minutes of fixations kept while the receiver is unreachable


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat()


class Receiver:
    def __init__(self, url: str, token_file: Path, timeout: float = 2.0) -> None:
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.timeout = timeout
        self.queue: deque[dict] = deque(maxlen=QUEUE)
        self.opener = build_opener(ProxyHandler({}))  # never route localhost through a system proxy
        self.refusal: str | None = None  # why the receiver last refused a report, for the log
        self.live = False  # the receiver wants the live gaze point: something on screen shows it
        self.ring = False  # the receiver wants the ring drawn over every window (gaze.overlay)
        self.offset_grid: dict | None = None  # its click correction, for that ring (see ``corrected``)
        self.token: str | None = None
        self._next: tuple[float, float, float] | None = None  # the live point waiting to go out
        self._busy = False
        self._wake = threading.Condition()
        self._sender: threading.Thread | None = None
        self.live_ms: deque[float] = deque(maxlen=300)  # round trips of recent live posts, for the state
        self.lines: deque[dict] = deque(maxlen=2000)  # reading-line labels from the receiver (``take_lines``)
        self.reading: dict | None = None  # the line the receiver's decoder has the eyes on now (``_reading``)

    def add(self, fixation: Fixation) -> str:
        """Queue a fixation; returns its id (the receiver names it again in a line label)."""
        fixation_id = uuid.uuid4().hex
        self.queue.append({
            "id": fixation_id, "t": iso(fixation.start), "t_end": iso(fixation.end),
            "x": round(fixation.x, 5), "y": round(fixation.y, 5), "samples": fixation.samples,
        })  # fmt: skip
        return fixation_id

    def take_lines(self) -> list[dict]:
        """Line labels the receiver sent since the last call: the fixation's id, and the line's
        middle height and horizontal extent as monitor fractions."""
        out = list(self.lines)
        self.lines.clear()
        return out

    def send(self, state: dict) -> str:
        """POST /gaze. Returns ok, paused (the queue is dropped: nothing is kept while paused),
        offline or refused (the queue is kept)."""
        batch = list(self.queue)[:BATCH]
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError:
            return "refused"
        request = Request(
            f"{self.url}/gaze",
            data=json.dumps({"state": state, "fixations": batch}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read()
        except HTTPError as error:
            self.live = self.ring = False
            self.reading = None
            if error.code == 409:
                self.queue.clear()
                return "paused"
            try:
                body = error.read(300).decode("utf-8", "replace")
            except OSError:
                body = ""
            self.refusal = f"{error.code} {body}".strip()
            return "refused"
        except (URLError, OSError):
            self.live = self.ring = False
            self.reading = None
            return "offline"
        try:
            answer = json.loads(body)
            counts = [answer.get(key) for key in ("accepted", "duplicates", "stale")]
            if not all(type(n) is int and n >= 0 for n in counts) or sum(counts) != len(batch):
                raise ValueError("invalid fixation acknowledgement")
        except (ValueError, AttributeError, TypeError):
            self.live = self.ring = False
            self.offset_grid = self.reading = None
            self.refusal = "invalid fixation acknowledgement"
            return "refused"
        for _ in batch:
            self.queue.popleft()
        self.token = token
        try:
            self.live, self.ring = bool(answer.get("live")), bool(answer.get("ring"))
            self.offset_grid = _grid(answer.get("offset_grid"))
            self.lines.extend(_lines(answer.get("lines")))
            self.reading = _reading(answer.get("reading"))
        except (ValueError, AttributeError):
            self.live = self.ring = False
            self.offset_grid = self.reading = None
        return "ok"

    def send_live(self, t: float, x: float, y: float) -> None:
        """Queue the smoothed gaze point now (monitor fractions) for POST /gaze/live. Returns at
        once; best effort: a lost point is replaced by the next frame's."""
        if self.token is None:
            return
        with self._wake:
            self._next = (t, x, y)
            if self._sender is None:
                self._sender = threading.Thread(target=self._send_live_forever, name="live gaze", daemon=True)
                self._sender.start()
            self._wake.notify()

    def flush(self, timeout: float = 2.0) -> bool:
        """Wait until the queued live point has gone out (tests)."""
        with self._wake:
            return self._wake.wait_for(lambda: self._next is None and not self._busy, timeout)

    def _send_live_forever(self) -> None:
        url = urlsplit(self.url)
        connection: HTTPConnection | None = None
        while True:
            with self._wake:
                self._wake.wait_for(lambda: self._next is not None)
                (t, x, y), self._next, self._busy = self._next, None, True
                token = self.token
            body = json.dumps({"t": iso(t), "x": round(x, 5), "y": round(y, 5)}).encode("utf-8")
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
            started = time.perf_counter()
            for attempt in range(2):  # the server may have closed an idle kept-alive connection
                try:
                    if connection is None:
                        connection = HTTPConnection(url.hostname or "127.0.0.1", url.port or 80, timeout=0.25)
                    connection.request("POST", url.path.rstrip("/") + "/gaze/live", body, headers)
                    response = connection.getresponse()
                    response.read()
                    if response.status >= 400:
                        self.live = False  # paused, or an older receiver without the endpoint
                    if response.getheader("Connection", "").lower() == "close" or response.version == 10:
                        connection.close()
                        connection = None
                    break
                except (OSError, HTTPException):
                    if connection is not None:
                        connection.close()
                    connection = None
                    if attempt:
                        break
            self.live_ms.append((time.perf_counter() - started) * 1000)
            with self._wake:
                self._busy = False
                self._wake.notify_all()

    def paused(self) -> bool | None:
        """The receiver's pause state, or None when it is not running."""
        try:
            with self.opener.open(f"{self.url}/status", timeout=self.timeout) as response:
                return bool(json.loads(response.read())["paused"])
        except (URLError, OSError, ValueError, KeyError):
            return None

    def control(self, action: str) -> bool:
        """POST /pause or /resume; False when the receiver is not running or refuses."""
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
            request = Request(f"{self.url}/{action}", data=b"", headers={"Authorization": f"Bearer {token}"})
            with self.opener.open(request, timeout=self.timeout) as response:
                response.read()
        except (URLError, OSError):
            return False
        return True


def _grid(value: object) -> dict | None:
    """The receiver's click-correction grid, if well formed."""
    if not isinstance(value, dict):
        return None
    cols, rows, dx, dy = value.get("cols"), value.get("rows"), value.get("dx"), value.get("dy")
    if not (isinstance(cols, int) and isinstance(rows, int) and cols >= 2 and rows >= 2):
        return None
    if not (isinstance(dx, list) and isinstance(dy, list) and len(dx) == len(dy) == cols * rows):
        return None
    if not all(isinstance(v, int | float) for v in (*dx, *dy)):
        return None
    return {"cols": cols, "rows": rows, "dx": [float(v) for v in dx], "dy": [float(v) for v in dy]}


def corrected(grid: dict | None, fx: float, fy: float) -> tuple[float, float]:
    """A gaze point (monitor fractions) with the receiver's click correction taken off, the
    correction interpolated between the grid's points as the receiver would compute it there."""
    if grid is None:
        return fx, fy
    cols, rows = grid["cols"], grid["rows"]
    gx = min(max(fx, 0.0), 1.0) * (cols - 1)
    gy = min(max(fy, 0.0), 1.0) * (rows - 1)
    i, j = min(int(gx), cols - 2), min(int(gy), rows - 2)
    u, v = gx - i, gy - j

    def at(values: list[float]) -> float:
        a, b = values[j * cols + i], values[j * cols + i + 1]
        c, d = values[(j + 1) * cols + i], values[(j + 1) * cols + i + 1]
        return (1 - u) * (1 - v) * a + u * (1 - v) * b + (1 - u) * v * c + u * v * d

    return fx - at(grid["dx"]), fy - at(grid["dy"])


def _lines(value: object) -> list[dict]:
    """Well-formed line labels of a /gaze answer: id, and y, x0, x1 in monitor fractions."""
    out = []
    for item in value if isinstance(value, list) else []:
        try:
            label = {"id": str(item["id"]), "y": float(item["y"]), "x0": float(item["x0"]), "x1": float(item["x1"])}
        except (KeyError, TypeError, ValueError):
            continue
        if all(-0.5 <= label[k] <= 1.5 for k in ("y", "x0", "x1")):
            out.append(label)
    return out


def _reading(value: object) -> dict | None:
    """The line being read, from a /gaze answer: when (epoch s, the fixation's end), its middle
    height and extent (monitor fractions) and the decoder's probability; None if absent or bad."""
    if not isinstance(value, dict):
        return None
    try:
        line = {k: float(value[k]) for k in ("t", "y", "x0", "x1", "p")}
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in line.values()) or not all(-0.5 <= line[k] <= 1.5 for k in ("y", "x0", "x1")):
        return None
    return line

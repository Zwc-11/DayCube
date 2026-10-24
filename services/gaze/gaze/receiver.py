"""Talking to the capture receiver on 127.0.0.1:7700: fixations and tracker state, once a second
(sooner when a fixation completes), and the live gaze point every frame while the receiver says
something shows it (the extension's gaze ring). Live points are not stored anywhere.

Live points go out from their own thread over one kept-alive connection, so the frame loop never
waits on the network (a POST that opens a connection costs 1-3 ms on Windows, at 30 a second);
a point not yet sent when the next arrives is replaced by it."""

from __future__ import annotations

import json
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
        self.token: str | None = None
        self._next: tuple[float, float, float] | None = None  # the live point waiting to go out
        self._busy = False
        self._wake = threading.Condition()
        self._sender: threading.Thread | None = None
        self.live_ms: deque[float] = deque(maxlen=300)  # round trips of recent live posts, for the state

    def add(self, fixation: Fixation) -> None:
        self.queue.append({
            "id": uuid.uuid4().hex, "t": iso(fixation.start), "t_end": iso(fixation.end),
            "x": round(fixation.x, 5), "y": round(fixation.y, 5), "samples": fixation.samples,
        })  # fmt: skip

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
            self.live = False
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
            self.live = False
            return "offline"
        for _ in batch:
            self.queue.popleft()
        self.token = token
        try:
            self.live = bool(json.loads(body).get("live"))
        except (ValueError, AttributeError):
            self.live = False
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

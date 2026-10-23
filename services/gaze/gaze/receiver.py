"""Talking to the capture receiver on 127.0.0.1:7700: fixations and tracker state, once a second."""

from __future__ import annotations

import json
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
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
                response.read()
        except HTTPError as error:
            if error.code == 409:
                self.queue.clear()
                return "paused"
            return "refused"
        except (URLError, OSError):
            return "offline"
        for _ in batch:
            self.queue.popleft()
        return "ok"

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

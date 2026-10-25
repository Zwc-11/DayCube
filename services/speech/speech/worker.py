"""Speech jobs leased from the capture receiver (docs/jobs.md): the receiver queues one for every
recording with speech that has none yet, and hands them out while the PC is free; this worker,
started by the receiver, runs them on the GPU and reports back.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path

import httpx

from .pipeline import Models, run_moment

STAGE = "speech"
HEARTBEAT_S = 40.0
log = logging.getLogger("speech")


class _Beat(threading.Thread):
    def __init__(self, client: httpx.Client, job_id: int, worker: str) -> None:
        super().__init__(name=f"lease {job_id}", daemon=True)
        self.client, self.job_id, self.worker = client, job_id, worker
        self.stopping = threading.Event()

    def run(self) -> None:
        while not self.stopping.wait(HEARTBEAT_S):
            try:
                if (
                    not self.client.post(f"/jobs/{self.job_id}/heartbeat", json={"worker": self.worker})
                    .json()
                    .get("ok")
                ):
                    log.warning("job %s: lease lost", self.job_id)
                    return
            except httpx.HTTPError as error:
                log.warning("job %s: heartbeat failed: %s", self.job_id, error)

    def stop(self) -> None:
        self.stopping.set()


def serve(vault_root: Path, receiver: str, *, once: bool = False, wait_s: float = 60.0,
          models: Models | None = None, say: Callable[[str], None] = log.info) -> int:  # fmt: skip
    """Lease and run speech jobs until stopped; ``once``: until none is left. Jobs done."""
    token = (vault_root / ".token").read_text(encoding="utf-8").strip()
    worker = f"speech@{socket.gethostname()}"
    client = httpx.Client(base_url=receiver, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    models = models or Models()
    done = 0
    while True:
        try:
            job = client.post("/jobs/lease", json={"worker": worker, "needs": "gpu", "stages": [STAGE]}).json()["job"]
        except (httpx.HTTPError, KeyError, ValueError) as error:
            if once:
                raise
            log.info("receiver not answering (%s); trying again in %.0f s", error, wait_s)
            time.sleep(wait_s)
            continue
        if job is None:
            if once:
                return done
            time.sleep(wait_s)
            continue
        beat = _Beat(client, job["id"], worker)
        beat.start()
        inputs = job["inputs"]
        try:
            outputs = run_moment(vault_root, inputs["date"], inputs["moment"], models, say)
        except FileNotFoundError as error:
            beat.stop()
            client.post(f"/jobs/{job['id']}/fail", json={"worker": worker, "error": str(error), "retry": False})
            continue
        except Exception as error:  # noqa: BLE001 - reported on the job; the receiver retries it
            beat.stop()
            log.exception("job %s failed", job["id"])
            reason = f"{type(error).__name__}: {error}\n{traceback.format_exc(limit=6)}"
            client.post(f"/jobs/{job['id']}/fail", json={"worker": worker, "error": reason[-4000:], "retry": True})
            continue
        beat.stop()
        client.post(f"/jobs/{job['id']}/done", json={"worker": worker, "outputs": outputs})
        done += 1
        say(f"speech of {inputs['date']} {inputs['moment']}: {outputs}")

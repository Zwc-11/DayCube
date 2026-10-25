"""The receiver runs the vault's jobs (``ingest.jobs``): one thread for CPU stages and one for the
GPU, which takes a job only while the PC is free (no input for 10 minutes, or at night). Every
minute each looks for what the vault needs (``discover``: a new scan's 3D) and runs jobs until none
is left. Their own ``Vault`` and ``Jobs`` objects, like the inbox's."""

from __future__ import annotations

import ctypes
import logging
import os
import socket
import threading
from pathlib import Path

log = logging.getLogger("capture.jobs")

EVERY_S = 60.0
FIRST_S = 60.0


def idle_seconds() -> float | None:
    """Seconds since the last keyboard or mouse input (Windows), or None where unknown."""
    if os.name != "nt":
        return None

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LastInput()
    info.cbSize = ctypes.sizeof(LastInput)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):  # type: ignore[attr-defined]
        return None
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000  # type: ignore[attr-defined]


class JobRunner(threading.Thread):
    def __init__(self, vault_root: Path, needs: str, every_s: float = EVERY_S) -> None:
        super().__init__(name=f"jobs {needs}", daemon=True)
        self.vault_root, self.needs, self.every_s = vault_root, needs, every_s
        self.worker = f"receiver-{needs}@{socket.gethostname()}"
        self.stopping = threading.Event()

    def run(self) -> None:
        from ingest.jobs.stages import discover
        from ingest.jobs.store import Jobs
        from ingest.jobs.worker import run_next
        from ingest.vault import Vault

        if self.stopping.wait(FIRST_S):
            return
        vault, jobs = Vault(self.vault_root), Jobs.of(self.vault_root)
        try:
            while not self.stopping.is_set():
                try:
                    if self.needs == "cpu":
                        for job_id in discover(vault, jobs):
                            log.info("jobs: queued %s", job_id)
                    while not self.stopping.is_set():
                        result = run_next(vault, jobs, self.worker, self.needs, idle_s=idle_seconds(), say=log.info)
                        if result is None:
                            break
                        log.info("jobs: %s %s %s%s", result["did"], result["stage"], result["inputs"],
                                 f": {result['error']}" if result.get("error") else "")  # fmt: skip
                except Exception:  # noqa: BLE001 - logged; the next round goes on
                    log.exception("running jobs failed")
                self.stopping.wait(self.every_s)
        finally:
            jobs.close()

    def stop(self) -> None:
        self.stopping.set()

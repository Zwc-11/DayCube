"""Run the eye tracker (``services/gaze``, its own environment) as a child of the receiver.

The tracker needs numpy<2 through EyeTrax and MediaPipe, while the rest of DayCube needs
numpy>=2, so it cannot share this process. The receiver starts it, restarts it when it stops,
and stops it on exit, so one autostart entry covers both.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from ingest.config import PROJECT_ROOT

log = logging.getLogger("capture.gaze")

NOT_CALIBRATED = 3  # the tracker's exit code when vault/calibration/gaze.json is missing or unusable
OTHER_MONITOR = 4  # the primary monitor is not the calibrated one (undocked, or a monitor off)
CALIBRATION = Path("calibration") / "gaze.json"


def gaze_python(root: Path = PROJECT_ROOT) -> Path | None:
    venv = root / "services" / "gaze" / ".venv"
    for candidate in (venv / "Scripts" / "pythonw.exe", venv / "Scripts" / "python.exe", venv / "bin" / "python"):
        if candidate.is_file():
            return candidate
    return None


class GazeSupervisor(threading.Thread):
    def __init__(self, python: Path, vault_root: Path, receiver: str) -> None:
        super().__init__(name="gaze-supervisor", daemon=True)
        self.command = [
            str(python),
            "-m",
            "gaze",
            "track",
            "--vault",
            str(vault_root),
            "--receiver",
            receiver,
            "--supervised",
        ]
        self.calibration = vault_root / CALIBRATION
        self.stopping = threading.Event()
        self.process: subprocess.Popen | None = None

    def run(self) -> None:
        delay = 5.0
        while not self.stopping.is_set():
            if not self.calibration.is_file():
                log.info(
                    "eye tracker waits for calibration: run `uv run --project services/gaze python -m gaze calibrate`"
                )
                while not self.calibration.is_file() and not self.stopping.wait(10):
                    pass
                continue
            started = time.monotonic()
            try:
                flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
                self.process = subprocess.Popen(
                    self.command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )  # fmt: skip
            except OSError as error:
                log.error("eye tracker failed to start: %s", error)
                self.stopping.wait(300)
                continue
            code = self.process.wait()
            if self.stopping.is_set():
                break
            if code == NOT_CALIBRATED:  # the calibration is unusable: wait for a new one
                log.warning("eye tracker rejected the calibration; waiting for `python -m gaze calibrate`")
                seen = self._stamp()
                while self._stamp() == seen and not self.stopping.wait(10):
                    pass
                continue
            if code == OTHER_MONITOR:
                log.info("eye tracker off: the calibrated monitor is not the primary one; checking again in a minute")
                self.stopping.wait(60)
                continue
            # A tracker that ran for a while gets a quick restart; one that keeps failing backs off.
            delay = 5.0 if time.monotonic() - started > 300 else min(delay * 2, 300.0)
            log.warning("eye tracker exited with code %s; restarting in %.0f s", code, delay)
            self.stopping.wait(delay)

    def _stamp(self) -> int | None:
        try:
            return self.calibration.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def stop(self) -> None:
        self.stopping.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()

"""Turns the vault's LiDAR scans into 3D while the receiver runs (``ingest.splat.queue``): a new scan
gets its LiDAR preview within minutes, and is trained into a Gaussian splat on the GPU when you
have been away from the PC for ten minutes, or at night, once Brush is installed. Its own ``Vault``
object, like the inbox's."""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("capture.scans")

EVERY_S = 300.0
FIRST_S = 120.0


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


class SceneMaker(threading.Thread):
    def __init__(self, vault_root: Path, every_s: float = EVERY_S) -> None:
        super().__init__(name="scenes", daemon=True)
        self.vault_root, self.every_s = vault_root, every_s
        self.stopping = threading.Event()

    def run(self) -> None:
        if self.stopping.wait(FIRST_S):
            return
        while True:
            try:
                from ingest.splat.queue import step
                from ingest.vault import Vault

                result = step(Vault(self.vault_root), idle_s=idle_seconds(), say=log.info)
                if result.get("did"):
                    log.info("scenes: %s %s", result["did"], result["moment"])
                    continue  # more may wait: look again at once
            except Exception:  # noqa: BLE001 - logged, in the moment's scene.json too; the next round goes on
                log.exception("making a scan's 3D failed")
            if self.stopping.wait(self.every_s):
                return

    def stop(self) -> None:
        self.stopping.set()

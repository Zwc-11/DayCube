"""Makes memories (``ingest.recon.memory``, docs/memories.md) while the receiver runs, so nothing has
to be double-clicked: every minute it looks in ``vault/inbox/memories``, and photos that have
stopped changing become memories in the day they were taken. With no memories in the vault yet, the
sharpest frames of the newest scan are made into memories, once per start.

SHARP needs PyTorch and the GPU, in ``services/recon``'s environment, so it runs as a child process
(no window, below-normal priority), its output appended to ``vault/logs/memories.log``. It takes
about 6 s a photo on the GPU, so unlike a scan's training it does not wait for the PC to be free.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from ingest.config import PROJECT_ROOT

log = logging.getLogger("capture.memories")

EVERY_S = 60.0
FIRST_S = 45.0
SETTLED_S = 10.0  # a photo still being copied in waits for the next round
TIMEOUT_S = 1800.0
BACKOFF_S = 1800.0  # after a run that failed, before the next
SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}  # as ingest.recon.memory reads
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def recon_python(root: Path = PROJECT_ROOT) -> Path | None:
    venv = root / "services" / "recon" / ".venv"
    for candidate in (venv / "Scripts" / "python.exe", venv / "bin" / "python"):
        if candidate.is_file():
            return candidate
    return None


def waiting(vault_root: Path, now: float | None = None) -> list[Path]:
    """Photos in the inbox that have not changed for ``SETTLED_S``."""
    inbox = vault_root / "inbox" / "memories"
    if not inbox.is_dir():
        return []
    now = time.time() if now is None else now
    return sorted(
        p for p in inbox.iterdir()
        if p.is_file() and p.suffix.lower() in SUFFIXES and now - p.stat().st_mtime >= SETTLED_S
    )  # fmt: skip


def have_memories(vault_root: Path) -> bool:
    return any(vault_root.glob("????-??-??/memories/mem_*/memory.json"))


def have_scans(vault_root: Path) -> bool:
    return any(vault_root.glob("????-??-??/moments/mo_scan_*/scene.json"))


class MemoryMaker(threading.Thread):
    def __init__(self, vault_root: Path, python: Path, every_s: float = EVERY_S, first_s: float = FIRST_S) -> None:
        super().__init__(name="memories", daemon=True)
        self.vault_root, self.python, self.every_s, self.first_s = vault_root, python, every_s, first_s
        self.stopping = threading.Event()
        self.scan_tried = False
        self.failed_at: float | None = None
        self.process: subprocess.Popen | None = None

    def plan(self, now: float | None = None) -> list[str] | None:
        """The arguments for ``ingest.recon.memory`` this round, or None when there is nothing to make."""
        now = time.time() if now is None else now
        if self.failed_at is not None and now - self.failed_at < BACKOFF_S:
            return None
        arguments = []
        if waiting(self.vault_root, now):
            arguments.append("--inbox")
        if not self.scan_tried and not have_memories(self.vault_root) and have_scans(self.vault_root):
            arguments += ["--scan", "latest", "--count", "6", "--if-none"]
        return arguments or None

    def step(self, now: float | None = None) -> int | None:
        """Run one round; the child's exit code, or None when there was nothing to make."""
        arguments = self.plan(now)
        if arguments is None:
            return None
        if "--scan" in arguments:
            self.scan_tried = True  # once per start, made or not: the log says why
        logs = self.vault_root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        command = [str(self.python), "-m", "ingest.recon.memory", *arguments]
        env = os.environ | {
            "DAYCUBE_VAULT_ROOT": str(self.vault_root),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        log.info("memories: making (%s)", " ".join(arguments))
        with open(logs / "memories.log", "a", encoding="utf-8") as out:
            out.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] receiver: {' '.join(command[1:])}\n")
            out.flush()
            self.process = subprocess.Popen(command, cwd=PROJECT_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                            stderr=subprocess.STDOUT, creationflags=NO_WINDOW)  # fmt: skip
            try:
                code = self.process.wait(timeout=TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self.process.kill()
                code = self.process.wait()
            finally:
                self.process = None
        if code == 0:
            self.failed_at = None
            log.info("memories: made (vault/logs/memories.log)")
        else:
            self.failed_at = time.time() if now is None else now
            log.warning("memories: the maker stopped with %s, trying again in %d min (vault/logs/memories.log)",
                        code, BACKOFF_S // 60)  # fmt: skip
        return code

    def run(self) -> None:
        if self.stopping.wait(self.first_s):
            return
        while True:
            try:
                self.step()
            except Exception:  # noqa: BLE001 - logged; the next round tries again
                log.exception("making memories failed")
                self.failed_at = time.time()
            if self.stopping.wait(self.every_s):
                return

    def stop(self) -> None:
        self.stopping.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()

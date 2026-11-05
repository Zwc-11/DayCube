"""Keeps the running receiver on this checkout's code: after ``git pull`` (or a patch) it restarts
itself on the new code the next time you are away.

The receiver runs the code it started with (``restart.py``). Every ``EVERY_S`` this thread compares
the stamp of the code it loaded with the stamp of the files on disk (the capture package and the
ingest and gaze packages it runs). When they differ it restarts once all of these hold:

- the files have not changed since the last look, so a pull or a patch is not half-written;
- you are away (the screen reader's away: no real keyboard or mouse input and no gaze for five
  minutes, or the lock screen), so the gaze ring and the hotkey never vanish under your hands;
- the new code imports, checked in a process of its own: a checkout that does not import leaves
  the running receiver as it is, and the log, ``/status`` and the doctor say why;
- this code has not been tried in the last hour (``vault/logs/update.json``), so a restart that
  does not take is not repeated every few minutes.

The restart is ``python -m capture restart --unattended``, started outside this process's tree
(through a process that exits at once), since stopping the receiver stops its children too. It
starts the receiver's own command line again (noted in ``update.json``, so that the restart's own
command line never looks like a receiver's) and logs the outcome to ``capture.log``.
``DAYCUBE_AUTO_RESTART=false`` turns this off; ``python -m capture restart`` works by hand as before.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .restart import NO_WINDOW, code_stamp

log = logging.getLogger("capture.update")

EVERY_S = 300.0
RETRY_S = 3600.0  # the same code is not tried again sooner
IMPORT_TIMEOUT_S = 120.0
# What the receiver imports at start and in its first rounds: a checkout that fails here would not run.
IMPORTS = ("capture.__main__", "capture.api", "capture.librarian", "capture.screen", "capture.inputs",
           "capture.doingnow", "capture.update", "ingest.review.summary", "ingest.review.metrics",
           "ingest.review.weekly", "ingest.notes")  # fmt: skip
# Starts the command given after it, detached and windowless, and exits: the command's parent is
# then a process that has ended, outside the tree ``taskkill /T`` ends.
LAUNCHER = (
    "import subprocess, sys; subprocess.Popen(sys.argv[1:], close_fds=True, stdin=subprocess.DEVNULL, "
    "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
    "creationflags=0x08000208 if sys.platform == 'win32' else 0, start_new_session=sys.platform != 'win32')"
)


def check_imports(python: str = sys.executable, timeout_s: float = IMPORT_TIMEOUT_S) -> str | None:
    """None when the checkout's code imports in a fresh interpreter, else the error's last lines."""
    try:
        done = subprocess.run(
            [python, "-c", "import " + ", ".join(IMPORTS)], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout_s, creationflags=NO_WINDOW if sys.platform == "win32" else 0,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"{type(error).__name__}: {error}"
    if done.returncode == 0:
        return None
    lines = [line for line in done.stderr.strip().splitlines() if line.strip()]
    return " | ".join(lines[-3:])[-600:] or f"exit code {done.returncode}"


def spawn_apart(command: list[str], python: str = sys.executable) -> None:
    """Start ``command`` outside this process's tree, so ending this receiver does not end it."""
    subprocess.run(
        [python, "-c", LAUNCHER, *command], capture_output=True, timeout=60,
        creationflags=NO_WINDOW if sys.platform == "win32" else 0,
    )  # fmt: skip


def noted_command(vault_root: Path) -> str | None:
    """The command line the updater asked the restart to start (``update.json``)."""
    try:
        command = json.loads((vault_root / "logs" / "update.json").read_text(encoding="utf-8")).get("command")
    except (OSError, ValueError, AttributeError):
        return None
    return command if isinstance(command, str) and command.strip() else None


def own_command() -> str:
    """This receiver's command line, to start again as it was started."""
    return subprocess.list2cmdline([sys.executable, "-m", "capture", *sys.argv[1:]])


class Updater(threading.Thread):
    """Watches the checkout's code and restarts the receiver on it when you are away."""

    def __init__(
        self,
        vault_root: Path,
        loaded: str,
        away: Callable[[], bool],
        command: str,
        *,
        stamp: Callable[[], str] = code_stamp,
        imports: Callable[[], str | None] = check_imports,
        spawn: Callable[[list[str]], None] = spawn_apart,
        clock: Callable[[], float] = time.time,
        every_s: float = EVERY_S,
    ) -> None:
        super().__init__(name="code updater", daemon=True)
        self.tried_file = vault_root / "logs" / "update.json"
        self.loaded, self.away, self.command = loaded, away, command
        self.stamp, self.imports, self.spawn, self.clock, self.every_s = stamp, imports, spawn, clock, every_s
        self.pending: str | None = None  # the checkout's stamp at the last look, while it differs
        self.problem: str | None = None  # why the checkout's code was not started
        self.last: str | None = None  # what the last look found
        self.stopping = threading.Event()

    def run(self) -> None:
        while not self.stopping.wait(self.every_s):
            try:
                self.check()
            except Exception:  # noqa: BLE001 - logged; the next look tries again
                log.exception("the checkout's code could not be checked")

    def stop(self) -> None:
        self.stopping.set()

    def check(self) -> str:
        """One look. Returns what it found: ``current`` (running the checkout's code), ``changing``
        (it changed since the last look), ``present`` (you are here), ``tried`` (restarted on this
        code within the hour), ``broken`` (it does not import) or ``restarting``."""
        self.last = self._check()
        return self.last

    def _check(self) -> str:
        current = self.stamp()
        if current == self.loaded:
            self.pending = self.problem = None
            return "current"
        if current != self.pending:
            if self.pending is None:
                log.info("this checkout's code changed (%s; running %s): restarting on it when you are away",
                         current, self.loaded)  # fmt: skip
            self.pending, self.problem = current, None
            return "changing"
        if not self.away():
            return "present"
        tried = self._tried()
        if tried.get("stamp") == current and self.clock() - float(tried.get("at") or 0) < RETRY_S:
            return "tried"
        problem = self.imports()
        if problem:
            if self.problem != problem:
                log.warning("not restarting on this checkout's code (%s): it does not import: %s", current, problem)
            self.problem = problem
            return "broken"
        self._remember(current)
        log.info("restarting on this checkout's code (%s; was %s)", current, self.loaded)
        self.spawn([sys.executable, "-m", "capture", "restart", "--unattended"])
        return "restarting"

    def _tried(self) -> dict:
        try:
            tried = json.loads(self.tried_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return tried if isinstance(tried, dict) else {}

    def _remember(self, stamp: str) -> None:
        try:
            self.tried_file.parent.mkdir(parents=True, exist_ok=True)
            noted = {"stamp": stamp, "at": self.clock(), "from": self.loaded, "command": self.command}
            self.tried_file.write_text(json.dumps(noted), encoding="utf-8")
        except OSError as error:
            log.warning("could not note the restart in %s: %s", self.tried_file, error)

    def state(self) -> dict:
        return {"running": self.is_alive(), "waiting": self.pending, "last": self.last, "problem": self.problem}


def start(receiver: object, vault_root: Path, loaded: str) -> Updater:
    """The updater for this receiver, running."""
    updater = Updater(vault_root, loaded, receiver.person_away, own_command())  # type: ignore[attr-defined]
    updater.start()
    receiver.updater = updater  # type: ignore[attr-defined]
    return updater

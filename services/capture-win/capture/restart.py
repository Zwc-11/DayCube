"""Restarting the receiver, and the eye tracker it runs, on the code in this checkout.

The receiver started at sign-in keeps running the code it started with until it is stopped, so
after ``git pull`` it runs older code than the checkout. ``python -m capture restart`` stops it and
its eye tracker and starts the sign-in command again; ``install-autostart`` does the same when the
running receiver's code differs from the checkout's. ``/status`` reports a stamp of the code the
receiver loaded (``code``), compared with the stamp of the files on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

PACKAGE = Path(__file__).parent
# `python -m capture run ...` (and the older `python -m capture --receive`), and the eye tracker the
# receiver supervises. Never `capture restart`, `doctor` or `install-autostart`: those are commands.
RECEIVER = re.compile(r"(?:^|\s)-m\s+capture(?:\s+run\b|\s+--)")
TRACKER = re.compile(r"(?:^|\s)-m\s+gaze\s+track\b.*\s--supervised\b")
NO_WINDOW = 0x08000000


def code_stamp(folder: Path = PACKAGE) -> str:
    """A stamp of the receiver's code: its Python files' names and contents."""
    digest = hashlib.blake2b(digest_size=6)
    for path in sorted(folder.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def processes() -> list[dict]:
    """Python processes on this computer: id, parent id and command line (Windows, via CIM)."""
    script = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        creationflags=NO_WINDOW if sys.platform == "win32" else 0,
    )  # fmt: skip
    found = json.loads(result.stdout or "[]")
    found = [found] if isinstance(found, dict) else found
    return [
        {
            "pid": int(p["ProcessId"]),
            "parent": int(p.get("ParentProcessId") or 0),
            "command": p.get("CommandLine") or "",
        }
        for p in found
    ]


def ours(found: list[dict], me: int) -> list[int]:
    """The receivers and supervised eye trackers among ``found``, never this process or the ones
    that started it (a venv's python.exe is a launcher with the same command line as its child)."""
    parents = {p["pid"]: p["parent"] for p in found}
    mine, pid = set(), me
    while pid and pid not in mine:
        mine.add(pid)
        pid = parents.get(pid, 0)
    return [
        p["pid"] for p in found
        if p["pid"] not in mine and (RECEIVER.search(p["command"]) or TRACKER.search(p["command"]))
    ]  # fmt: skip


def stop(pids: list[int]) -> None:
    """End these processes and their children (the tracker is the receiver's child)."""
    if not pids:
        return
    arguments = ["taskkill", "/T", "/F"]
    for pid in pids:
        arguments += ["/PID", str(pid)]
    subprocess.run(arguments, capture_output=True, timeout=60, creationflags=NO_WINDOW)


def restart(
    command: list[str] | str,
    status: Callable[[], dict | None],
    start: Callable[[list[str] | str], None],
    *,
    find: Callable[[], list[dict]] = processes,
    end: Callable[[list[int]], None] = stop,
    wait_s: float = 45.0,
    sleep: Callable[[float], None] = time.sleep,
    me: int | None = None,
) -> tuple[list[int], dict | None]:
    """Stop the running receiver and tracker, start ``command``, and wait for the new receiver to
    answer. Returns the processes stopped and the new receiver's status (None: it did not answer)."""
    pids = ours(find(), os.getpid() if me is None else me)
    end(pids)
    for _ in range(40):  # the port is free once the old receiver is gone
        if status() is None:
            break
        sleep(0.25)
    start(command)
    waited = 0.0
    while waited < wait_s:  # imports and the first vault read take a few seconds
        sleep(0.5)
        waited += 0.5
        answer = status()
        if answer is not None:
            return pids, answer
    return pids, None

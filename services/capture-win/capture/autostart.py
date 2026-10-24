"""Start the capture service at every Windows sign-in, hidden, until shutdown.

Uses the per-user ``Run`` registry key: no administrator rights, no script host, and it shows
under Task Manager > Startup apps, where it can be turned off like any other startup app. The
command runs the venv's ``pythonw.exe``, so no console window opens.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "DayCube capture"


def _registry() -> ModuleType:
    if sys.platform != "win32":
        raise SystemExit("Autostart uses the Windows sign-in Run key; run this command on Windows.")
    import winreg

    return winreg


def launch_command(*, gaze: bool, on_dwell: str | None = None, interpreter: Path | None = None) -> list[str]:
    python = interpreter or Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    command = [str(windowless if windowless.is_file() else python), "-m", "capture", "run", "--receive"]
    if gaze:
        command.append("--gaze")
    if on_dwell:
        command += ["--on-dwell", on_dwell]
    return command


def install(command: list[str]) -> str:
    winreg = _registry()
    line = subprocess.list2cmdline(command)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, line)
    return line


def uninstall() -> bool:
    winreg = _registry()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE)
    except FileNotFoundError:
        return False
    return True


def current() -> str | None:
    winreg = _registry()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            return winreg.QueryValueEx(key, VALUE)[0]
    except FileNotFoundError:
        return None


def start_detached(command: list[str] | str) -> None:
    """Start the service now, outliving this console, as the next sign-in would."""
    flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW
    subprocess.Popen(
        command, creationflags=flags if sys.platform == "win32" else 0, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=sys.platform != "win32",
    )  # fmt: skip

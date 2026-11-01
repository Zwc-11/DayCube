"""Alt+Shift+N: a one-line box, "What are you doing now?", whose answer goes to ``doing.jsonl``.

The cheapest truth there is about the day: a line in your own words, at the moment. The day's
review lists the lines with their times and can cite them; the timeline's categories stay what the
rules say (a line is a note, not a relabel).

- The hotkey is registered with ``RegisterHotKey`` on this module's own thread: no keyboard hook,
  so typing is never slowed. ``MOD_NOREPEAT`` keeps a held key from opening the box again.
- The box is a small always-on-top Tk window in a process of its own (``python -m
  capture.doingnow ask``): Tk does not belong in a receiver full of threads, where a Tk object
  freed on the wrong thread can end the process. Enter saves, Escape or a minute without an answer
  closes it. Only one box is open at a time.
- ``python -m capture doing "..."`` and ``POST /doing`` (the vault token) write the same line.
- Paused recording keeps nothing (the receiver drops it).
"""

from __future__ import annotations

import ctypes
import json
import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger("capture.doingnow")

HOTKEY_ID = 0x0DC1
MOD_ALT, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0004, 0x4000
VK_N = 0x4E
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
ANSWER_S = 60.0  # the box closes by itself after this long without an answer
PROMPT = "What are you doing now?"


def ask_tk(timeout_s: float = ANSWER_S) -> str | None:
    """The box. Returns the line typed, or None (Escape, closed, or no answer in time)."""
    import tkinter as tk

    result: list[str | None] = [None]
    root = tk.Tk()
    root.title("DayCube")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    frame = tk.Frame(root, padx=14, pady=12)
    frame.pack()
    tk.Label(frame, text=PROMPT, anchor="w").pack(fill="x")
    entry = tk.Entry(frame, width=52)
    entry.pack(pady=(6, 0))

    def save(_event: object = None) -> None:
        result[0] = entry.get()
        root.destroy()

    def cancel(_event: object = None) -> None:
        root.destroy()

    entry.bind("<Return>", save)
    root.bind("<Escape>", cancel)
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.after(int(timeout_s * 1000), cancel)
    root.update_idletasks()
    width, height = root.winfo_width(), root.winfo_height()
    x = (root.winfo_screenwidth() - width) // 2
    y = root.winfo_screenheight() // 3 - height // 2
    root.geometry(f"+{x}+{y}")
    root.after(30, lambda: (root.lift(), root.focus_force(), entry.focus_set()))
    root.mainloop()
    text = (result[0] or "").strip()
    return text or None


def ask_apart(timeout_s: float = ANSWER_S) -> str | None:
    """The box, in a process of its own; its answer comes back as one line of JSON."""
    try:
        done = subprocess.run(
            [sys.executable, "-m", "capture.doingnow", "ask"], capture_output=True, text=True, timeout=timeout_s + 10,
            creationflags=0x08000000 if sys.platform == "win32" else 0,  # CREATE_NO_WINDOW
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("the doing-now box did not answer: %s", error)
        return None
    for line in reversed(done.stdout.splitlines()):
        try:
            answer = json.loads(line)
        except ValueError:
            continue
        return answer.get("text") if isinstance(answer, dict) else None
    if done.returncode:
        log.warning("the doing-now box failed: %s", done.stderr.strip()[-300:])
    return None


class DoingBox(threading.Thread):
    """Waits for the hotkey and opens the box. ``save`` gets each line; ``ask`` shows the box."""

    def __init__(self, save: Callable[[str], Any], ask: Callable[[], str | None] = ask_apart) -> None:
        super().__init__(name="doing-now box", daemon=True)
        self.save, self.ask = save, ask
        self.error: str | None = None
        self.ready = threading.Event()
        self.thread_id: int | None = None
        self.opened = 0
        self.saved = 0
        self._open = threading.Lock()

    def on_hotkey(self) -> threading.Thread | None:
        """Open the box unless one is open; returns the box's thread."""
        if not self._open.acquire(blocking=False):
            return None
        self.opened += 1
        thread = threading.Thread(target=self._ask, name="doing-now prompt", daemon=True)
        thread.start()
        return thread

    def _ask(self) -> None:
        try:
            text = self.ask()
            if text:
                self.save(text)
                self.saved += 1
        except Exception:  # noqa: BLE001 - a failed box must not stop the hotkey
            log.exception("the doing-now box failed")
        finally:
            self._open.release()

    def run(self) -> None:
        try:
            self._run()
        except Exception as error:  # noqa: BLE001
            self.error = f"{type(error).__name__}: {error}"
            log.warning("no Alt+Shift+N box: %s", self.error)
        finally:
            self.ready.set()

    def _run(self) -> None:
        if sys.platform != "win32":
            raise OSError("the hotkey needs Windows")
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_ALT | MOD_SHIFT | MOD_NOREPEAT, VK_N):
            raise ctypes.WinError(ctypes.get_last_error())  # 1409: another program has Alt+Shift+N
        self.thread_id = kernel32.GetCurrentThreadId()
        self.ready.set()
        log.info("Alt+Shift+N opens the doing-now box")
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.on_hotkey()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self) -> None:
        if self.thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)

    def state(self) -> dict:
        return {"running": self.is_alive() and self.error is None, "error": self.error, "opened": self.opened,
                "saved": self.saved}  # fmt: skip


def start(receiver: Any) -> tuple[DoingBox | None, str | None]:
    """The hotkey's thread, running, or None and why not."""
    box = DoingBox(lambda text: receiver.doing_now(text, source="hotkey"))
    if sys.platform != "win32":
        return None, "the hotkey needs Windows"
    box.start()
    box.ready.wait(5)
    if box.error:
        return None, box.error
    receiver.doing = box
    return box, None


if __name__ == "__main__":
    if sys.argv[1:] == ["ask"]:
        print(json.dumps({"text": ask_tk()}), flush=True)

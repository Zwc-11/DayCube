"""How much typing and mousing there is, not what: counts per 10 s into ``capture/<date>/inputs.jsonl``.

The day's timeline knows what was in front; it cannot tell typing from reading from sitting idle
with a window open. These counts can: keys pressed, mouse buttons pressed, wheel notches and how
far the mouse moved, in 10-second buckets, written only for buckets with any input.

- **Windows Raw Input.** ``RegisterRawInputDevices`` with ``RIDEV_INPUTSINK`` on a hidden window of
  its own thread gets every key and mouse event whichever app is in front. Unlike a low-level hook
  (``WH_KEYBOARD_LL``) it is not in the input chain: it cannot slow typing, and Windows never drops
  it for answering late.
- **No key identities.** The callback passes each event to ``Counts``, which keeps only the keys
  held down at this moment (so a held key's auto-repeat is one press) and counters. Which keys
  were pressed is never written anywhere; a test reads every file the receiver writes for one.
- **10-second buckets** say enough to tell typing from reading in a stretch of the day without
  making a password's length readable from one second's count. While the window in front is one
  the screen reader leaves out (password managers, the lock and sign-in screens, the apps
  excluded in its settings), nothing is counted.
- **Paused** recording writes nothing (the receiver drops the bucket).
- **People, not software.** Input that a program sends (``SendInput``: automation and AI agents
  driving apps, keep-awake tools pressing F15, remote control) reaches Raw Input with no device
  (``hDevice`` 0). It is not counted as yours: a bucket keeps it apart as ``injected``, and the
  screen reader's "away" goes by ``idle_given`` (the time since the last key, click or move from a
  real device), so a PC that software keeps busy all night is not a person at the desk. Remote
  Desktop and the on-screen keyboard send input the same way, and count as software too.
- **Input the counter cannot see** is not taken for absence. When Windows' own idle time says
  input came later than anything the counter got, from a device or from a program (input to a
  window of a program run as administrator, which Windows may keep from a process that is not;
  touch and pen, which are not counted), Windows' time is the one used.

``python -m capture inputs`` (not Windows: says so) prints the counts for 30 s, for checking.
"""

from __future__ import annotations

import ctypes
import logging
import math
import struct
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("capture.inputs")

BUCKET_S = 10.0
SLACK_S = 2.0  # Windows' idle time and the counter's clock agree within this
MAX_HELD = 32  # keys held at once that are remembered for auto-repeat; more is a stuck state
# RAWMOUSE.usButtonFlags: a press of each button (releases are not counted)
DOWN_FLAGS = (0x0001, 0x0004, 0x0010, 0x0040, 0x0100)  # left, right, middle, X1, X2
WHEEL, HWHEEL = 0x0400, 0x0800
MOUSE_MOVE_ABSOLUTE = 0x0001
RI_KEY_BREAK = 0x0001
RIM_TYPEMOUSE, RIM_TYPEKEYBOARD = 0, 1
ANY_DOWN = sum(DOWN_FLAGS)
ANY_WHEEL = WHEEL | HWHEEL
# RAWINPUT, unpacked in place from the window procedure's buffer (compiled once, no copy per event).
# The header is dwType, dwSize, then hDevice and wParam, pointer-sized: 24 bytes on 64-bit Windows.
HEADERS = {24: struct.Struct("<I4xQ"), 16: struct.Struct("<I4xI")}
KEYBOARD = struct.Struct("<2xH2xH")  # RAWKEYBOARD: MakeCode, Flags, Reserved, VKey
MOUSE = struct.Struct("<H2xHH4xii")  # RAWMOUSE: usFlags, usButtonFlags, usButtonData, lLastX, lLastY


@dataclass
class Counts:
    """Counters for the bucket under way. ``key`` takes a key's code only to know whether it is
    already held; the code is dropped on release and never leaves this object."""

    keys: int = 0
    clicks: int = 0
    wheel: int = 0
    move_px: float = 0.0
    _held: set[int] = field(default_factory=set, repr=False)
    _last_abs: tuple[int, int] | None = field(default=None, repr=False)

    def key(self, code: int, released: bool) -> None:
        if released:
            self._held.discard(code)
            return
        if code in self._held:
            return  # auto-repeat of a held key
        if len(self._held) >= MAX_HELD:
            self._held.clear()  # releases were missed (sleep, a secure desktop): start over
        self._held.add(code)
        self.keys += 1

    def mouse(self, flags: int, buttons: int, data: int, dx: int, dy: int) -> None:
        """One RAWMOUSE: ``flags`` its usFlags, ``buttons`` usButtonFlags, ``data`` usButtonData
        (signed: wheel delta, 120 a notch), ``dx``/``dy`` lLastX/lLastY. Nearly every call is a
        bare relative move, so that case does one addition."""
        if buttons:
            if buttons & ANY_DOWN:
                self.clicks += sum(1 for bit in DOWN_FLAGS if buttons & bit)
            if buttons & ANY_WHEEL:
                delta = data - 0x10000 if data >= 0x8000 else data
                self.wheel += max(1, round(abs(delta) / 120))
        if flags & MOUSE_MOVE_ABSOLUTE:
            # Tablets and remote sessions: positions on a 0..65535 desktop, not moves. Their
            # distance is counted in those units scaled to a 2560-pixel-wide screen.
            if self._last_abs is not None:
                ax, ay = self._last_abs
                self.move_px += math.hypot(dx - ax, dy - ay) * 2560 / 65536
            self._last_abs = (dx, dy)
        elif dx or dy:
            self.move_px += math.hypot(dx, dy)

    def any(self) -> bool:
        return bool(self.keys or self.clicks or self.wheel or self.move_px >= 1)

    def take(self) -> dict[str, int]:
        """The bucket's counts, and zero for the next one (keys held stay held)."""
        out = {"keys": self.keys, "clicks": self.clicks, "wheel": self.wheel, "move_px": round(self.move_px)}
        self.keys = self.clicks = self.wheel = 0
        self.move_px = 0.0
        return out


def parse(buffer: bytes, header_size: int) -> tuple[int, tuple, int] | None:
    """A RAWINPUT as GetRawInputData returns it: (RIM_TYPEKEYBOARD, (vkey, released), device) or
    (RIM_TYPEMOUSE, (flags, buttons, data, dx, dy), device), ``device`` the header's hDevice (0 for
    input a program sent); None for other devices or a short buffer."""
    got = len(buffer)
    header = HEADERS.get(header_size)
    if header is None or got < header_size:
        return None
    kind, device = header.unpack_from(buffer, 0)
    if kind == RIM_TYPEKEYBOARD and got >= header_size + 16:
        key_flags, vkey = KEYBOARD.unpack_from(buffer, header_size)
        return kind, (vkey, bool(key_flags & RI_KEY_BREAK)), device
    if kind == RIM_TYPEMOUSE and got >= header_size + 24:
        return kind, MOUSE.unpack_from(buffer, header_size), device
    return None


def dispatch(buffer: Any, got: int, header_size: int, buckets: Buckets) -> None:
    """What the window procedure does with each WM_INPUT: read the RAWINPUT that GetRawInputData
    wrote into ``buffer`` (``got`` bytes) where it lies, and count it. The same reading as ``parse``
    without copying the buffer or building tuples: this runs for every mouse report."""
    header = HEADERS.get(header_size)
    if header is None or got < header_size:
        return
    kind, device = header.unpack_from(buffer, 0)
    if kind == RIM_TYPEMOUSE:
        if got >= header_size + 24:
            buckets.mouse(*MOUSE.unpack_from(buffer, header_size), device)
    elif kind == RIM_TYPEKEYBOARD and got >= header_size + 16:
        key_flags, vkey = KEYBOARD.unpack_from(buffer, header_size)
        buckets.key(vkey, bool(key_flags & RI_KEY_BREAK), device)


class Buckets:
    """Turns events into one record per bucket with input. ``emit`` gets each record; ``private``
    says whether the window in front is one whose input is not counted."""

    def __init__(
        self,
        emit: Callable[[dict], None],
        private: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.time,
        bucket_s: float = BUCKET_S,
    ) -> None:
        self.emit, self.private, self.clock, self.bucket_s = emit, private, clock, bucket_s
        self.counts = Counts()
        self.started = clock()
        self.start = self._floor(self.started)
        self.lock = threading.Lock()
        self.written = 0
        self.skipped_private = 0  # events not counted: a private window in front
        self.last_at: float | None = None
        self.last_human: float | None = None  # the last event from a real device
        self.injected = 0  # events a program sent, in the bucket under way
        self.injected_total = 0
        self.injected_at: float | None = None

    def _floor(self, t: float) -> float:
        return t - t % self.bucket_s

    def event(self, kind: int, values: tuple, device: int = 1) -> None:
        """One raw input event as ``parse`` returns it; ``device`` 0: a program sent it."""
        if kind == RIM_TYPEKEYBOARD:
            self.key(*values, device)
        elif kind == RIM_TYPEMOUSE:
            self.mouse(*values, device)

    def _injected(self, now: float) -> None:
        self.injected += 1
        self.injected_total += 1
        self.injected_at = now

    def key(self, vkey: int, released: bool, device: int = 1) -> None:
        now = self.clock()
        with self.lock:
            if now >= self.start + self.bucket_s:
                self._roll(now)
            if not device:
                self._injected(now)
                return
            self.last_human = now
            if not released and self.private():
                self.skipped_private += 1
                return
            self.counts.key(vkey, released)

    def mouse(self, flags: int, buttons: int, data: int, dx: int, dy: int, device: int = 1) -> None:
        now = self.clock()
        with self.lock:
            if now >= self.start + self.bucket_s:
                self._roll(now)
            if not device:
                self._injected(now)
                return
            self.last_human = now
            if buttons and self.private():  # a press or the wheel; a bare move says nothing
                self.skipped_private += 1
                return
            self.counts.mouse(flags, buttons, data, dx, dy)

    def idle_s(self) -> float:
        """Seconds since the last key, click or move from a real device (since the counter started
        when there has been none)."""
        with self.lock:
            return max(0.0, self.clock() - (self.last_human if self.last_human is not None else self.started))

    def idle_given(self, system_idle_s: float, slack_s: float = SLACK_S) -> tuple[float, str]:
        """Seconds since the person last used the PC, given Windows' idle time, and what decided:
        ``devices`` (a real keyboard or mouse; Windows saw nothing later), ``software`` (the input
        Windows saw later was sent by a program), ``unseen`` (Windows saw input later that reached
        the counter neither way; Windows' time is taken)."""
        with self.lock:
            now = self.clock()
            devices = max(0.0, now - (self.last_human if self.last_human is not None else self.started))
            if system_idle_s >= devices - slack_s:
                return devices, "devices"
            if self.injected_at is not None and now - self.injected_at <= system_idle_s + slack_s:
                return devices, "software"
            return max(0.0, system_idle_s), "unseen"

    def tick(self) -> None:
        """Called about once a second: a bucket that has ended is written even with no new event."""
        with self.lock:
            self._roll(self.clock())

    def _roll(self, now: float) -> None:
        if now < self.start + self.bucket_s:
            return
        if self.counts.any() or self.injected:
            begun = datetime.fromtimestamp(self.start, UTC).astimezone()  # no mktime: Windows refuses it near 1970
            record = {"t": begun.isoformat(timespec="seconds"), "s": int(self.bucket_s), **self.counts.take(),
                      "injected": self.injected}  # fmt: skip
            try:
                self.emit(record)
                self.written += 1
                self.last_at = now
            except Exception:  # noqa: BLE001 - a failed write must not stop counting
                log.exception("input counts could not be written")
        self.counts.take()
        self.injected = 0
        self.start = self._floor(now)


# -- Windows -------------------------------------------------------------------------------------


class InputCounter(threading.Thread):
    """The hidden window and its message loop, on its own thread. ``buckets`` gets every event."""

    def __init__(self, buckets: Buckets) -> None:
        super().__init__(name="input counter", daemon=True)
        if sys.platform != "win32":
            raise OSError("counting input needs Windows")
        self.buckets = buckets
        self.error: str | None = None
        self.ready = threading.Event()
        self.thread_id: int | None = None

    def run(self) -> None:
        try:
            self._run()
        except Exception as error:  # noqa: BLE001 - the receiver goes on without the counts
            self.error = f"{type(error).__name__}: {error}"
            log.exception("input counting stopped")
        finally:
            self.ready.set()

    def _run(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        lresult = ctypes.c_ssize_t
        wndproc_t = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", wndproc_t),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                        ("hIconSm", wintypes.HICON)]  # fmt: skip

        class RAWINPUTDEVICE(ctypes.Structure):
            _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT), ("dwFlags", wintypes.DWORD),
                        ("hwndTarget", wintypes.HWND)]  # fmt: skip

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = lresult
        user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                           ctypes.POINTER(wintypes.UINT), wintypes.UINT]  # fmt: skip
        user32.GetRawInputData.restype = wintypes.UINT
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]  # fmt: skip
        user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
        header_size = 8 + 2 * ctypes.sizeof(ctypes.c_void_p)  # dwType, dwSize, hDevice, wParam
        WM_INPUT, WM_TIMER, WM_CLOSE, WM_DESTROY = 0x00FF, 0x0113, 0x0010, 0x0002
        RID_INPUT, RIDEV_INPUTSINK = 0x10000003, 0x00000100
        buffer = ctypes.create_string_buffer(256)
        size = wintypes.UINT()

        def proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
            if message == WM_INPUT:
                size.value = ctypes.sizeof(buffer)
                got = user32.GetRawInputData(lparam, RID_INPUT, buffer, ctypes.byref(size), header_size)
                if got not in (0, 0xFFFFFFFF):
                    dispatch(buffer, got, header_size, self.buckets)
                return user32.DefWindowProcW(hwnd, message, wparam, lparam)
            if message == WM_TIMER:
                self.buckets.tick()
                return 0
            if message == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._proc = wndproc_t(proc)  # kept alive for the window's life
        instance = kernel32.GetModuleHandleW(None)
        name = "DayCubeInputCounter"
        klass = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._proc, hInstance=instance,
                            lpszClassName=name)  # fmt: skip
        if not user32.RegisterClassExW(ctypes.byref(klass)) and ctypes.get_last_error() != 1410:  # already
            raise ctypes.WinError(ctypes.get_last_error())
        # A hidden top-level window (never shown): RIDEV_INPUTSINK needs a target window.
        hwnd = user32.CreateWindowExW(0, name, "DayCube input counter", 0, 0, 0, 0, 0, None, None, instance, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        devices = (RAWINPUTDEVICE * 2)(RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, hwnd),  # keyboard
                                       RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd))  # mouse  # fmt: skip
        if not user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RAWINPUTDEVICE)):
            raise ctypes.WinError(ctypes.get_last_error())
        user32.SetTimer(hwnd, 1, 1000, None)
        self.hwnd = hwnd
        self.thread_id = kernel32.GetCurrentThreadId()
        self.ready.set()
        log.info("counting keys and mouse input in %d s buckets (counts only)", int(self.buckets.bucket_s))
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self) -> None:
        if self.thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT

    def state(self) -> dict:
        b = self.buckets

        def at(t: float | None) -> str | None:
            return datetime.fromtimestamp(t, UTC).astimezone().isoformat(timespec="seconds") if t else None

        return {"running": self.is_alive() and self.error is None, "error": self.error, "buckets": b.written,
                "last": at(b.last_at), "skipped_private": b.skipped_private, "idle_s": round(b.idle_s(), 1),
                "injected": b.injected_total, "injected_last": at(b.injected_at)}  # fmt: skip


def private_front(reader: Any) -> Callable[[], bool]:
    """Whether the window in front is one the screen reader leaves out for privacy: a password
    manager, the lock or sign-in screen, an app excluded in its settings, a private title. The
    answer is kept for a second: input events come far more often than windows change."""
    from .screen import why_not

    cache: list[float | bool] = [0.0, False]

    def check() -> bool:
        now = time.monotonic()
        if now - float(cache[0]) < 1.0:
            return bool(cache[1])
        answer = False
        try:
            window = reader.system.foreground()
            if window is not None:
                _, extra = reader.options()
                answer = why_not(window, extra) in ("locked", "excluded", "private")
        except Exception:  # noqa: BLE001 - unknown: count (counts say nothing of content)
            answer = False
        cache[0], cache[1] = now, answer
        return answer

    return check


def start(receiver: Any) -> tuple[InputCounter | None, str | None]:
    """The counter, running, or None and why not. Records go to ``receiver.input_counts``."""
    reader = getattr(receiver, "screen", None)
    private = private_front(reader) if reader is not None else (lambda: False)
    try:
        counter = InputCounter(Buckets(receiver.input_counts, private))
    except OSError as error:
        return None, str(error)
    counter.start()
    counter.ready.wait(5)
    if counter.error:
        return None, counter.error
    return counter, None


def demo(seconds: float = 30.0) -> int:
    """``python -m capture inputs``: prints each bucket as it closes."""
    counter, problem = None, None
    try:
        counter = InputCounter(Buckets(lambda r: print(r, flush=True), bucket_s=5.0))
    except OSError as error:
        problem = str(error)
    if counter is None:
        print("Not counting:", problem)
        return 1
    counter.start()
    counter.ready.wait(5)
    if counter.error:
        print("Not counting:", counter.error)
        return 1
    print(f"Counting for {seconds:.0f} s in 5 s buckets; type and move the mouse.")
    time.sleep(seconds)
    counter.stop()
    return 0

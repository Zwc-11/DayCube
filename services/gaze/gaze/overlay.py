"""The gaze ring drawn over every window: a small always-on-top, click-through window that the
tracker moves to where the eyes are (Windows).

The extension's ring lives inside web pages, so it shows only on the active tab of a browser
window on the calibrated monitor, never over other apps or the browser's own pages, and moves
when a page asks for the point (30 times a second at most). This one is drawn by the tracker
process itself:

- **Everywhere.** A layered ``WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW |
  WS_EX_NOACTIVATE`` popup: above other windows, invisible to the mouse, never takes focus, not in
  the taskbar or Alt-Tab, and left out of screenshots and screen sharing
  (``WDA_EXCLUDEFROMCAPTURE``), so DayCube's own screen capture never reads it.
- **Every display frame.** Its thread waits for the compositor (``DwmFlush``) and moves the window
  once per frame of the monitor (60, 120 or 144 Hz), whatever the camera's rate.
- **Smooth between gaze points.** Points arrive once per camera frame (30 or 60 a second). Each
  display frame shows the gaze one sample interval ago, interpolated between the two points around
  it (``Trail``): motion is continuous at any refresh rate, for one sample interval of delay
  (33 ms at 30 fps, 17 ms at 60), in place of the page ring's polling and easing.
- **No network.** Points go from the tracking thread to the overlay thread in memory.

The ring fades when no point has come for 0.6 s (a blink, the face lost) and goes after 4 s; it is
dashed at the monitor's edge while the gaze is off it.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol

import numpy as np

log = logging.getLogger("gaze")

RING_PX = 32  # the window: the ring and its outline, with room for anti-aliasing
FADE_S = 0.6
HIDE_S = 4.0
FADED = 0.35
MIN_DELAY_S, MAX_DELAY_S = 0.008, 0.06


# -- the ring ---------------------------------------------------------------------------------------


def ring_pixels(
    size: int = RING_PX,
    radius: float = 11.0,
    stroke: float = 2.5,
    rgb: tuple[int, int, int] = (239, 68, 68),
    dashed: bool = False,
) -> np.ndarray:
    """The ring as premultiplied BGRA (what ``UpdateLayeredWindow`` takes): a red stroke over a
    faint red fill, with a thin white outline so it shows on dark and light backgrounds alike.
    Anti-aliased by distance from the circle."""
    centre = size / 2
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5 - centre
    r = np.hypot(xx, yy)
    stroke_a = np.clip(stroke / 2 + 0.5 - np.abs(r - radius), 0, 1) * 0.9
    if dashed:  # 12 dashes, softened at their ends
        turn = (np.arctan2(yy, xx) / (2 * np.pi) * 12) % 1.0
        stroke_a *= np.clip((0.6 - turn) * r * 0.5, 0, 1) * np.clip(turn * r * 0.5 + 0.5, 0, 1)
    fill_a = np.clip(radius - stroke / 2 + 0.5 - r, 0, 1) * 0.15
    outline_a = np.clip(1.0 - np.abs(r - (radius + stroke / 2 + 0.75)), 0, 1) * 0.7
    red = np.array(rgb[::-1], dtype=np.float32)  # BGR
    white = np.full(3, 255.0, dtype=np.float32)
    # Premultiplied "over", bottom to top: outline, fill, stroke.
    colour = outline_a[..., None] * white
    alpha = outline_a.copy()
    for a in (fill_a, stroke_a):
        colour = a[..., None] * red + colour * (1 - a[..., None])
        alpha = a + alpha * (1 - a)
    out = np.empty((size, size, 4), dtype=np.uint8)
    out[..., :3] = np.clip(np.round(colour), 0, 255)
    out[..., 3] = np.clip(np.round(alpha * 255), 0, 255)
    return out


# -- where to draw it -------------------------------------------------------------------------------


class Trail:
    """Recent gaze points, and where the ring goes at a display frame's time.

    The ring shows the gaze ``delay`` ago, linearly between the two points around that moment;
    ``delay`` is the median interval between recent points, so the moment almost always has a point
    on each side. Thread-safe: the tracker adds, the overlay thread reads."""

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self.clock = clock
        self._points: deque[tuple[float, float, float, bool]] = deque(maxlen=16)
        self._lock = threading.Lock()

    def add(self, x: float, y: float, on_screen: bool = True, t: float | None = None) -> None:
        with self._lock:
            self._points.append((self.clock() if t is None else t, x, y, on_screen))

    def clear(self) -> None:
        with self._lock:
            self._points.clear()

    def delay(self, points: list[tuple[float, float, float, bool]] | None = None) -> float:
        if points is None:
            with self._lock:
                points = list(self._points)
        gaps = [b[0] - a[0] for a, b in zip(points[-9:], points[-8:], strict=False) if b[0] > a[0]]
        if not gaps:
            return MAX_DELAY_S
        return min(MAX_DELAY_S, max(MIN_DELAY_S, float(np.median(gaps))))

    def position(self, now: float | None = None) -> tuple[float, float, float, bool] | None:
        """(x, y, opacity, on screen) for a frame shown at ``now``, or None: nothing to show."""
        now = self.clock() if now is None else now
        with self._lock:
            points = list(self._points)
        if not points:
            return None
        age = now - points[-1][0]
        if age > HIDE_S:
            return None
        opacity = 1.0 if age <= FADE_S else FADED
        at = now - self.delay(points)
        if at >= points[-1][0]:
            _, x, y, on = points[-1]
            return x, y, opacity, on
        if at <= points[0][0]:
            _, x, y, on = points[0]
            return x, y, opacity, on
        for (t0, x0, y0, _), (t1, x1, y1, on1) in zip(points, points[1:], strict=False):
            if t0 <= at <= t1:
                k = (at - t0) / (t1 - t0) if t1 > t0 else 1.0
                return x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, opacity, on1
        _, x, y, on = points[-1]
        return x, y, opacity, on


# -- drawing it ------------------------------------------------------------------------------------


class Backend(Protocol):
    """A window that shows the ring (``Win32Backend``; tests use a fake)."""

    refresh_hz: float

    def show(self, dashed: bool, opacity: float) -> None: ...
    def move(self, left: int, top: int) -> None: ...
    def hide(self) -> None: ...
    def wait(self) -> None: ...
    def pump(self) -> None: ...
    def close(self) -> None: ...


class Overlay:
    """The ring's window and its thread. ``push`` gaze points (monitor fractions) from any
    thread; the overlay thread draws one frame per display refresh while there is something to
    show, and idles otherwise."""

    def __init__(
        self,
        rect: tuple[int, int, int, int],
        backend: Callable[[], Backend] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        start: bool = True,
    ) -> None:
        self.left, self.top, self.width, self.height = rect
        self.clock = clock
        self.trail = Trail(clock)
        self.error: str | None = None
        self.refresh_hz: float | None = None
        self._factory = backend or Win32Backend
        self._backend: Backend | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._shown: tuple[bool, float] | None = None  # (dashed, opacity) on screen, None when hidden
        self._at: tuple[int, int] | None = None
        self._drawn: deque[float] = deque()  # display frames drawn with the ring on screen, last 2 s
        self._moved: deque[float] = deque()  # frames on which it moved
        self._thread: threading.Thread | None = None
        if start:
            self._thread = threading.Thread(target=self._run, name="gaze overlay", daemon=True)
            self._thread.start()
            self._ready.wait(5.0)

    @property
    def running(self) -> bool:
        return self._backend is not None and not self._stop.is_set()

    def push(self, fx: float, fy: float) -> None:
        """A gaze point in fractions of the monitor; off it, the ring waits dashed at its edge."""
        on = 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0
        x = self.left + min(max(fx, 0.0), 1.0) * self.width
        y = self.top + min(max(fy, 0.0), 1.0) * self.height
        self.trail.add(x, y, on)

    def clear(self) -> None:
        """Nothing to show (tracking held, the ring switched off): the window hides at its next frame."""
        self.trail.clear()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def state(self) -> dict:
        """For the tracker's state: whether the ring is on screen, frames drawn per second while it
        is, and how often it moved (at most the display's refresh rate)."""
        now = self.clock()
        drawn = sum(1 for t in self._drawn if now - t <= 1.0)
        moved = sum(1 for t in self._moved if now - t <= 1.0)
        return {
            "running": self.running,
            "shown": self._shown is not None,
            "fps": drawn if self._shown is not None else None,
            "moves_per_s": moved if self._shown is not None else None,
            "refresh_hz": self.refresh_hz,
            "error": self.error,
        }

    # -- the overlay thread ------------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._backend = self._factory()
            self.refresh_hz = self._backend.refresh_hz
        except Exception as error:  # noqa: BLE001 - the tracker keeps working without the ring
            self.error = f"{type(error).__name__}: {error}"
            log.warning("the gaze ring overlay is unavailable: %s", self.error)
            self._ready.set()
            return
        self._ready.set()
        log.info("gaze ring overlay running (display %.0f Hz)", self.refresh_hz or 0)
        try:
            while not self._stop.is_set():
                if not self.step():
                    time.sleep(0.05)  # nothing to show: no need to wake every display frame
        except Exception as error:  # noqa: BLE001
            self.error = f"{type(error).__name__}: {error}"
            log.exception("the gaze ring overlay stopped")
        finally:
            backend, self._backend = self._backend, None
            with contextlib.suppress(Exception):
                backend.close()

    def step(self) -> bool:
        """One display frame: place the ring for now, then wait for the next frame. False when
        there was nothing to show (the caller idles)."""
        backend = self._backend
        assert backend is not None
        backend.pump()
        now = self.clock()
        where = self.trail.position(now)
        if where is None:
            if self._shown is not None:
                backend.hide()
                self._shown, self._at = None, None
            return False
        x, y, opacity, on = where
        # Whole on the monitor: a ring at its edge (gaze off it) is not cut in half.
        at = (
            min(max(round(x - RING_PX / 2), self.left), self.left + self.width - RING_PX),
            min(max(round(y - RING_PX / 2), self.top), self.top + self.height - RING_PX),
        )
        if at != self._at:  # moved first: a ring appearing shows where the eyes are, not where it last was
            backend.move(*at)
            self._at = at
            self._moved.append(now)
        look = (not on, opacity)
        if self._shown != look:
            backend.show(*look)
            self._shown = look
        self._drawn.append(now)
        while self._drawn and now - self._drawn[0] > 2.0:
            self._drawn.popleft()
        while self._moved and now - self._moved[0] > 2.0:
            self._moved.popleft()
        backend.wait()
        return True


# -- Windows -----------------------------------------------------------------------------------------


def primary_rect() -> tuple[int, int, int, int] | None:
    """The primary monitor as (left, top, width, height) in physical pixels (the tracker's process
    is DPI aware), or None off Windows."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes as w

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MonitorFromPoint.argtypes = [w.POINT, w.DWORD]
    user32.MonitorFromPoint.restype = w.HANDLE
    user32.GetMonitorInfoW.argtypes = [w.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = w.BOOL
    monitor = user32.MonitorFromPoint(w.POINT(0, 0), 1)  # MONITOR_DEFAULTTOPRIMARY
    info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    r = info.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top


class Win32Backend:
    """The ring's window: created, drawn, moved and destroyed on the overlay thread (a window
    belongs to the thread that made it, which must also pump its messages)."""

    WS_POPUP = 0x80000000
    WS_EX = (
        0x00080000 | 0x00000020 | 0x00000008 | 0x00000080 | 0x08000000
    )  # LAYERED TRANSPARENT TOPMOST TOOLWINDOW NOACTIVATE
    SWP = 0x0001 | 0x0010 | 0x0200 | 0x0400  # NOSIZE NOACTIVATE NOOWNERZORDER NOSENDCHANGING
    TOPMOST_EVERY_S = 1.0  # other always-on-top windows may come above: claim the top again

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("the gaze ring overlay needs Windows")
        import ctypes
        from ctypes import wintypes as w

        self.ctypes, self.w = ctypes, w
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32, self.gdi32 = user32, gdi32
        try:
            self.dwmapi = ctypes.WinDLL("dwmapi")
            self.dwmapi.DwmFlush.restype = ctypes.c_long
            self.dwmapi.DwmFlush.argtypes = []
        except OSError:
            self.dwmapi = None

        LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", self.WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON),
            ]  # fmt: skip

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", w.BYTE), ("BlendFlags", w.BYTE), ("SourceConstantAlpha", w.BYTE),
                        ("AlphaFormat", w.BYTE)]  # fmt: skip

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG), ("biPlanes", w.WORD),
                ("biBitCount", w.WORD), ("biCompression", w.DWORD), ("biSizeImage", w.DWORD),
                ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD),
                ("biClrImportant", w.DWORD),
            ]  # fmt: skip

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", w.DWORD * 1)]

        self.BLENDFUNCTION = BLENDFUNCTION
        P = ctypes.POINTER
        user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.RegisterClassExW.argtypes = [P(WNDCLASSEXW)]
        user32.RegisterClassExW.restype = w.ATOM
        user32.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
        user32.CreateWindowExW.argtypes = [
            w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID,
        ]  # fmt: skip
        user32.CreateWindowExW.restype = w.HWND
        user32.DestroyWindow.argtypes = [w.HWND]
        user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
        user32.SetWindowPos.restype = w.BOOL
        user32.UpdateLayeredWindow.argtypes = [
            w.HWND, w.HDC, P(w.POINT), P(w.SIZE), w.HDC, P(w.POINT), w.COLORREF, P(BLENDFUNCTION), w.DWORD,
        ]  # fmt: skip
        user32.UpdateLayeredWindow.restype = w.BOOL
        user32.PeekMessageW.argtypes = [P(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
        user32.PeekMessageW.restype = w.BOOL
        user32.TranslateMessage.argtypes = [P(w.MSG)]
        user32.DispatchMessageW.argtypes = [P(w.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        user32.GetDC.argtypes = [w.HWND]
        user32.GetDC.restype = w.HDC
        user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
        gdi32.CreateCompatibleDC.argtypes = [w.HDC]
        gdi32.CreateCompatibleDC.restype = w.HDC
        gdi32.CreateDIBSection.argtypes = [w.HDC, P(BITMAPINFO), w.UINT, P(ctypes.c_void_p), w.HANDLE, w.DWORD]
        gdi32.CreateDIBSection.restype = w.HBITMAP
        gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
        gdi32.SelectObject.restype = w.HGDIOBJ
        gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [w.HDC]
        gdi32.GetDeviceCaps.argtypes = [w.HDC, ctypes.c_int]
        gdi32.GetDeviceCaps.restype = ctypes.c_int
        kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
        kernel32.GetModuleHandleW.restype = w.HMODULE

        self._proc = self.WNDPROC(lambda h, m, wp, lp: user32.DefWindowProcW(h, m, wp, lp))  # kept: Windows calls it
        self.instance = kernel32.GetModuleHandleW(None)
        self.class_name = f"DayCubeGazeRing{id(self)}"
        cls = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._proc, hInstance=self.instance,
                          lpszClassName=self.class_name)  # fmt: skip
        if not user32.RegisterClassExW(ctypes.byref(cls)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = user32.CreateWindowExW(
            self.WS_EX, self.class_name, "DayCube gaze ring", self.WS_POPUP, 0, 0, RING_PX, RING_PX,
            None, None, self.instance, None,
        )  # fmt: skip
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        # Left out of screenshots, recordings and screen sharing (Windows 10 2004+; best effort).
        user32.SetWindowDisplayAffinity.argtypes = [w.HWND, w.DWORD]
        user32.SetWindowDisplayAffinity(self.hwnd, 0x11)

        self.screen = user32.GetDC(None)
        self.refresh_hz = float(gdi32.GetDeviceCaps(self.screen, 116)) or 60.0  # VREFRESH; 0 or 1: unknown
        if self.refresh_hz < 24:
            self.refresh_hz = 60.0
        self.period = 1.0 / self.refresh_hz
        self.memory = gdi32.CreateCompatibleDC(self.screen)
        self.bitmaps = {}
        for dashed in (False, True):
            pixels = ring_pixels(dashed=dashed)
            # Top-down (negative height), 32 bits, BI_RGB.
            header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=RING_PX, biHeight=-RING_PX,
                                      biPlanes=1, biBitCount=32, biCompression=0)  # fmt: skip
            info = BITMAPINFO(bmiHeader=header)
            bits = ctypes.c_void_p()
            bitmap = gdi32.CreateDIBSection(self.screen, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not bitmap or not bits.value:
                raise ctypes.WinError(ctypes.get_last_error())
            ctypes.memmove(bits, pixels.ctypes.data, pixels.nbytes)
            self.bitmaps[dashed] = bitmap
        self._previous = gdi32.SelectObject(self.memory, self.bitmaps[False])
        self.at = (0, 0)
        self.visible = False
        self.topmost_at = 0.0
        self.frame_at = time.perf_counter()
        self.message = w.MSG()

    def show(self, dashed: bool, opacity: float) -> None:
        ctypes, w = self.ctypes, self.w
        self.gdi32.SelectObject(self.memory, self.bitmaps[dashed])
        blend = self.BLENDFUNCTION(0, 0, max(0, min(255, round(opacity * 255))), 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        where, size, origin = w.POINT(*self.at), w.SIZE(RING_PX, RING_PX), w.POINT(0, 0)
        ok = self.user32.UpdateLayeredWindow(  # ULW_ALPHA: per-pixel alpha, times the opacity
            self.hwnd, self.screen, ctypes.byref(where), ctypes.byref(size), self.memory, ctypes.byref(origin), 0,
            ctypes.byref(blend), 2,
        )  # fmt: skip
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.visible:
            self.user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
            self.visible = True
            self.topmost_at = 0.0

    def move(self, left: int, top: int) -> None:
        self.at = (left, top)
        self.user32.SetWindowPos(self.hwnd, self.w.HWND(-1), left, top, 0, 0, self.SWP)  # HWND_TOPMOST
        self.topmost_at = time.perf_counter()

    def hide(self) -> None:
        if self.visible:
            self.user32.ShowWindow(self.hwnd, 0)  # SW_HIDE
            self.visible = False

    def pump(self) -> None:
        message = self.message
        while self.user32.PeekMessageW(self.ctypes.byref(message), None, 0, 0, 1):  # PM_REMOVE
            self.user32.TranslateMessage(self.ctypes.byref(message))
            self.user32.DispatchMessageW(self.ctypes.byref(message))

    def wait(self) -> None:
        """Until the next display frame: the compositor's next pass (``DwmFlush``). When that
        returns early (nothing else changed on screen, or no compositor), the rest of the frame is
        slept, so the loop never spins faster than the display."""
        now = time.perf_counter()
        if self.visible and now - self.topmost_at > self.TOPMOST_EVERY_S:
            self.user32.SetWindowPos(self.hwnd, self.w.HWND(-1), 0, 0, 0, 0, self.SWP | 0x0002)  # NOMOVE
            self.topmost_at = now
        if self.dwmapi is not None:
            self.dwmapi.DwmFlush()
        rest = self.period * 0.9 - (time.perf_counter() - self.frame_at)
        if rest > 0:
            time.sleep(rest)
        self.frame_at = time.perf_counter()

    def close(self) -> None:
        user32, gdi32 = self.user32, self.gdi32
        gdi32.SelectObject(self.memory, self._previous)
        for bitmap in self.bitmaps.values():
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(self.memory)
        user32.ReleaseDC(None, self.screen)
        user32.DestroyWindow(self.hwnd)
        user32.UnregisterClassW(self.class_name, self.instance)


def create(rect: tuple[int, int, int, int] | None = None) -> Overlay | None:
    """The overlay on the primary monitor (check ``running``: if its window could not open,
    ``state()`` says why), or None where there is none to draw (not Windows)."""
    rect = rect or primary_rect()
    if rect is None:
        return None
    return Overlay(rect)


def demo(seconds: float = 3.0, rate: float = 30.0) -> int:
    """``python -m gaze.overlay``: the ring circles the primary monitor for ``seconds`` with points
    at ``rate`` a second, as the tracker sends them, and the frames drawn per second are printed.
    No camera is used."""
    import math

    ring = create()
    if ring is None or not ring.running:
        print("No ring:", "not Windows" if ring is None else ring.state()["error"])
        return 1
    start = time.perf_counter()
    shown: list[dict] = []
    try:
        while (t := time.perf_counter() - start) < seconds:
            ring.push(0.5 + 0.3 * math.cos(t * 2.5), 0.5 + 0.3 * math.sin(t * 2.5))
            if t > 1.0 and (not shown or t - shown[-1]["t"] >= 1.0):
                shown.append({"t": t, **ring.state()})
            time.sleep(1 / rate)
    finally:
        ring.close()
    for s in shown:
        print(f"{s['fps']} frames/s drawn on a {s['refresh_hz']:.0f} Hz display, moved {s['moves_per_s']} times")
    return 0


if __name__ == "__main__":
    sys.exit(demo(*(float(a) for a in sys.argv[1:3])))

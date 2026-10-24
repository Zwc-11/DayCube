"""What the computer is doing, for deciding when the webcam may be on (Windows; inert elsewhere)."""

from __future__ import annotations

import contextlib
import ctypes
import sys

WEBCAM_STORE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"


class System:
    def idle_seconds(self) -> float:
        """Seconds since the last keyboard or mouse input."""
        if sys.platform != "win32":
            return 0.0

        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LastInput(ctypes.sizeof(LastInput), 0)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0

    def locked(self) -> bool:
        """Whether the session is locked (or switched away): the input desktop is not ours."""
        if sys.platform != "win32":
            return False
        user32 = ctypes.windll.user32
        desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not desktop:
            return True
        try:
            name = ctypes.create_unicode_buffer(64)
            ok = user32.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), None)  # UOI_NAME
            return bool(ok) and name.value.lower() != "default"
        finally:
            user32.CloseDesktop(desktop)

    def camera_busy(self) -> bool:
        """Whether another app is using the webcam now, from Windows' camera privacy records:
        an app in use has a start time and no stop time. Best effort: False when unreadable.

        Python interpreters are skipped: a venv's python.exe starts the base interpreter, so this
        tracker is recorded under a path other than ``sys.executable``."""
        if sys.platform != "win32":
            return False
        import winreg

        def in_use(key: object) -> bool:
            try:
                start = winreg.QueryValueEx(key, "LastUsedTimeStart")[0]
                stop = winreg.QueryValueEx(key, "LastUsedTimeStop")[0]
            except OSError:
                return False
            return bool(start) and stop == 0

        def scan(path: str, skip_python: bool) -> bool:
            try:
                parent = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
            except OSError:
                return False
            with parent:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(parent, index)
                    except OSError:
                        return False
                    index += 1
                    if name == "NonPackaged" or (
                        skip_python and name.lower().endswith(("#python.exe", "#pythonw.exe"))
                    ):
                        continue
                    with winreg.OpenKey(parent, name) as key:
                        if in_use(key):
                            return True

        return scan(WEBCAM_STORE, False) or scan(WEBCAM_STORE + r"\NonPackaged", True)


def primary_monitor() -> tuple[int, int]:
    """The primary monitor's size in pixels: the calibration window opens there."""
    if sys.platform == "win32":
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()  # report physical pixels, as OpenCV draws them
    from screeninfo import get_monitors

    monitors = get_monitors()
    main = next((m for m in monitors if getattr(m, "is_primary", False)), monitors[0])
    return main.width, main.height


DEFAULT_MM_PER_PX = 0.25  # between a 24" 1920-px monitor (0.277) and a 27" 2560-px one (0.233)


def monitor_mm_per_px() -> tuple[float, str]:
    """The primary monitor's pixel pitch in millimetres and where it came from: Windows' raw DPI,
    which it computes from the size the monitor reports (EDID), or an assumed typical pitch. The
    head-aware gaze model needs it only roughly: 25% off costs 0-4 mm on screen (eval/head_bench.py)."""
    if sys.platform == "win32":
        with contextlib.suppress(AttributeError, OSError):
            user32, shcore = ctypes.windll.user32, ctypes.windll.shcore

            class Point(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            user32.MonitorFromPoint.restype = ctypes.c_void_p
            user32.MonitorFromPoint.argtypes = [Point, ctypes.c_uint]
            monitor = user32.MonitorFromPoint(Point(0, 0), 1)  # MONITOR_DEFAULTTOPRIMARY
            x, y = ctypes.c_uint(), ctypes.c_uint()
            if monitor and shcore.GetDpiForMonitor(ctypes.c_void_p(monitor), 2, ctypes.byref(x), ctypes.byref(y)) == 0:
                pitch = 25.4 / x.value if x.value else 0.0
                # A TV or projector may report no size (raw DPI 96, or nonsense): keep the default.
                if 0.1 <= pitch <= 0.6 and x.value != 96:
                    return pitch, "monitor"
    return DEFAULT_MM_PER_PX, "assumed"

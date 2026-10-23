"""``python -m capture doctor``: is capture working, judged from what it recorded while you used the
computer, so no test session is needed.

It asks the running receiver for its status, reads today's capture logs, and checks the sign-in
entry. Eye tracking is judged on four things the services measure continuously: frame rate, face
detection, how much gaze becomes fixations (``coverage``; a noisy session splits looks into pieces
too short to keep), and the error measured at clicks while browsing (each click is a moment when
the eyes are on a known point; see ``anchors.py``). The last is the one that says whether gaze can
tell paragraphs apart; it needs a few clicks first.

Exit code 1 when a check fails, 0 otherwise.
"""

from __future__ import annotations

import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

PASS, WARN, FAIL, INFO = "pass", "warn", "FAIL", "info"
# Median error at which paragraphs at 100% zoom stop being separable: a 1920 px wide 24" monitor at
# about 60 cm; scaled by monitor width. Matches services/gaze/gaze/calibrate.py.
USABLE_PX_AT_1920 = 120.0
LOGS = ("browser.jsonl", "blocks.jsonl", "gaze.jsonl", "attention.jsonl", "health.jsonl")


def _age(iso: str | None, now: datetime) -> float | None:
    return None if not iso else (now - datetime.fromisoformat(iso)).total_seconds()


def _count(path: Path) -> tuple[int, int]:
    """Lines and malformed lines; a partial last line (a write in progress) is not counted."""
    if not path.is_file():
        return 0, 0
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    complete = lines[:-1]  # every complete line ends with a newline
    bad = 0
    for line in complete:
        try:
            json.loads(line)
        except ValueError:
            bad += 1
    return len(complete), bad


def checks(status: dict | None, vault: Path, zone: ZoneInfo, now: datetime, autostart: str | None) -> list[tuple]:
    out: list[tuple[str, str, str]] = []
    if status is None:
        return [(FAIL, "receiver", "not running on 127.0.0.1:7700: start it, or sign out and in (autostart)")]
    out.append((WARN if status["paused"] else PASS, "receiver", "paused" if status["paused"] else "running"))

    age = _age(status.get("browser", {}).get("last"), now)
    if age is None:
        out.append((WARN, "browser", "no records since the receiver started: is the extension on and Opera open?"))
    else:
        out.append((PASS if age < 600 else INFO, "browser", f"last record {age / 60:.0f} min ago"))

    gaze = status.get("gaze", {})
    if not gaze.get("tracking"):
        reason = gaze.get("reason") or "unknown"
        normal = reason in ("paused", "away", "locked", "camera busy")
        out.append((INFO if normal else FAIL, "eye tracker", f"not tracking: {reason}"))
    else:
        fps, target = gaze.get("fps"), gaze.get("target_fps") or 30
        out.append((PASS if fps and fps >= 0.8 * target else WARN, "frame rate", f"{fps} of {target} frames/s"))
        face = gaze.get("face")
        verdict = PASS if face is not None and face >= 0.8 else WARN if face is not None and face >= 0.5 else FAIL
        out.append((verdict, "face found", "unknown" if face is None else f"{face:.0%} of frames"))
        coverage = gaze.get("coverage")
        if coverage is not None:
            verdict = PASS if coverage >= 0.6 else WARN if coverage >= 0.3 else FAIL
            noise = gaze.get("noise_px")
            out.append((verdict, "fixations", f"{coverage:.0%} of gaze" + (f", noise {noise:.0f} px" if noise else "")))

    monitor = gaze.get("monitor")
    limit = USABLE_PX_AT_1920 * (monitor[0] / 1920 if monitor else 1.0)
    error = gaze.get("error_px")
    if error is not None:
        verdict = PASS if error <= limit else FAIL
        out.append((verdict, "calibration", f"{error:.0f} px median held-out error (limit {limit:.0f})"))

    accuracy = status.get("accuracy") or {}
    clicks, corrected = accuracy.get("clicks", 0), accuracy.get("corrected_error_px")
    if clicks < 5 or corrected is None:
        out.append((INFO, "live accuracy", f"{clicks} of 5 clicks needed: browse and click links as usual"))
    else:
        raw = accuracy.get("live_error_px")
        verdict = PASS if corrected <= limit else WARN if corrected <= 1.5 * limit else FAIL
        detail = f"{corrected:.0f} px after correction, {raw:.0f} px before, from {clicks} clicks"
        if verdict != PASS:
            detail += ": recalibrate from your usual seat"
        out.append((verdict, "live accuracy", detail))
    rejected = accuracy.get("rejected", 0)
    if rejected and rejected >= max(5, clicks):
        out.append((WARN, "clicks", f"{rejected} clicks far from the gaze: the calibration may no longer fit"))

    day = vault / "capture" / now.astimezone(zone).date().isoformat()
    counts = {name: _count(day / name) for name in LOGS}
    malformed = sum(bad for _, bad in counts.values())
    summary = ", ".join(f"{name.split('.')[0]} {lines}" for name, (lines, _) in counts.items())
    out.append(
        (FAIL if malformed else PASS, "today's logs", summary + (f"; {malformed} malformed" if malformed else ""))
    )
    dwells = 0
    if (day / "attention.jsonl").is_file():
        for line in (day / "attention.jsonl").read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(ValueError):
                dwells += json.loads(line).get("phase") == "start"
    out.append((INFO, "dwells today", str(dwells)))

    if sys.platform == "win32":
        if autostart is None:
            out.append((WARN, "autostart", "not set: run python -m capture install-autostart"))
        else:
            program = autostart.split('"')[1] if autostart.startswith('"') else autostart.split(" ")[0]
            exists = Path(program).is_file()
            out.append((PASS if exists else FAIL, "autostart", autostart if exists else f"{program} is missing"))
    return out


def main(vault: Path, timezone: str, port: int) -> int:
    now = datetime.now(UTC)
    try:
        status = httpx.get(f"http://127.0.0.1:{port}/status", timeout=2).json()
    except (httpx.HTTPError, ValueError):
        status = None
    autostart = None
    if sys.platform == "win32":
        from . import autostart as entry

        autostart = entry.current()
    results = checks(status, vault, ZoneInfo(timezone), now, autostart)
    width = max(len(name) for _, name, _ in results)
    for verdict, name, detail in results:
        print(f"{verdict:>4}  {name:<{width}}  {detail}")
    return 1 if any(verdict == FAIL for verdict, _, _ in results) else 0

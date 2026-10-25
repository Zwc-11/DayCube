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
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .restart import code_stamp
from .supervise import speech_python

PASS, WARN, FAIL, INFO = "pass", "warn", "FAIL", "info"
# Median error at which paragraphs at 100% zoom stop being separable: a 1920 px wide 24" monitor at
# about 60 cm; scaled by monitor width. Matches services/gaze/gaze/calibrate.py.
USABLE_PX_AT_1920 = 120.0
LOGS = (
    "browser.jsonl",
    "blocks.jsonl",
    "gaze.jsonl",
    "attention.jsonl",
    "reading.jsonl",
    "health.jsonl",
    "windows.jsonl",
)
RESTART = "run python -m capture restart"


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


def _tail(path: Path, size: int = 16_000) -> list[str]:
    try:
        with path.open("rb") as file:
            file.seek(max(0, path.stat().st_size - size))
            return file.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def tracker_log(vault: Path) -> str | None:
    """Why the tracker is not running, from its own log (vault/logs/gaze.log: an error, a refused
    calibration) or the receiver's (capture.log: exit codes, waiting for a calibration): the last
    such line, without its timestamp."""
    lines = [line for line in _tail(vault / "logs" / "gaze.log") if " ERROR " in line or " WARNING " in line] + [
        line for line in _tail(vault / "logs" / "capture.log") if "capture.gaze" in line
    ]
    if not lines:
        return None
    last = max(lines, key=lambda line: line[:23])  # "2026-10-23 22:18:01,123": sortable
    text = last[24:].strip()
    for prefix in ("gaze ", "capture.gaze "):
        text = text.removeprefix(prefix)
    text = f"{last[:16]} {text}"  # when: the last such line can be from days before
    crash = _crash(_tail(vault / "logs" / "gaze.log"))
    if crash and crash not in text:
        text += f" (the tracker's error: {crash})"
    return text[:400]


def _crash(lines: list[str]) -> str | None:
    """The exception that last stopped the tracker: the line ending the traceback that follows its
    "stopped on an error" (``RuntimeError: ...``)."""
    start = max((i for i, line in enumerate(lines) if "stopped on an error" in line), default=None)
    if start is None:
        return None
    crash = None
    for line in lines[start + 1 :]:
        if re.match(r"^\d{4}-\d\d-\d\d ", line):
            break  # the next timestamped line: the traceback has ended
        if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Warning)\b", line):
            crash = line.strip()
    return crash


def _calibration(vault: Path) -> dict | None:
    try:
        saved = json.loads((vault / "calibration" / "gaze.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) else None


def _learning(gaze: dict) -> list[tuple[str, str, str]]:
    """Learning from clicks (services/gaze/gaze/learn.py), from the tracker's report."""
    learning = gaze.get("learning")
    if not gaze.get("tracking") or learning is None:
        return []
    if learning.get("error"):
        return [(WARN, "learning", f"failing: {learning['error']}")]
    clicks, skipped = learning.get("clicks", 0), learning.get("skipped") or {}
    fit, version = learning.get("fit"), learning.get("version", 0)
    away = skipped.get("not looking", 0)
    read = (learning.get("lines") or {}).get("used", 0)
    counted = (
        f"{clicks} clicks since calibration" + (f", {away} made without looking at the spot" if away else "")
        + (f"; {read} lines read as labels" if read else "")
    )  # fmt: skip
    if fit is None:
        return [(INFO, "learning", f"from clicks: {counted}; the first check needs 30 usable clicks")]
    errors = fit.get("error_px") or {}
    if version:
        when = (learning.get("updated") or "")[11:16]
        detail = (
            f"from clicks: update {version}" + (f" at {when} UTC" if when else "")
            + (" (eyes, and the head's place, distance and turn)" if learning.get("head_terms") else "")
            + f"; on clicks it had not seen, {errors.get('calibration')} px with the calibration alone, "
            f"{errors.get('learned') if fit.get('accepted') else errors.get('in_use')} px now; {counted}"
        )  # fmt: skip
        rows = [(PASS, "learning", detail)]
        applied = learning.get("applied") or {}
        if applied.get("version") == version:
            after = applied.get("after_ms")
            rows.append((PASS, "learning applied", f"update {version} reached the gaze"
                         + (f" {after:.0f} ms after it was fitted" if after is not None else "")
                         + " (every frame since uses it)"))  # fmt: skip
        elif "applied" in learning:  # a tracker that reports it: the update has not reached a frame yet
            rows.append((WARN, "learning applied", f"update {version} has not reached a frame yet"))
        live = learning.get("live") or {}
        if live.get("ratio") is not None:
            ratio = live["ratio"]
            rows.append((PASS if ratio < 1 else INFO, "loss ratio", (
                f"last {live['clicks']} clicks, each measured before learning from it: {live['error_px']} px "
                f"against {live['calibration_px']} px for the calibration alone (ratio {ratio:.2f}"
                + (f", {round((1 - ratio) * 100)}% less error)" if ratio < 1 else ")")
            )))  # fmt: skip
        return rows
    return [(
        INFO,
        "learning",
        f"from clicks: nothing better than the calibration yet ({errors.get('calibration')} px against "
        f"{errors.get('learned')} px on clicks it had not seen); {counted}",
    )]  # fmt: skip


def _drift(gaze: dict) -> list[tuple[str, str, str]]:
    """Whether the gaze slides while the eyes rest, and what slides with it (services/gaze/gaze/drift.py)."""
    drift = gaze.get("drift") if gaze.get("tracking") else None
    if not drift or drift.get("gaze_px_s") is None:
        return []
    rate = drift["gaze_px_s"]
    detail = (
        f"over the last {drift['stares']} stares ({drift['seconds']:.0f} s), the gaze slid {abs(rate):.1f} px/s "
        + ("down" if rate > 0 else "up")
    )
    if abs(rate) < 3:
        return [(PASS, "stare drift", detail + ": steady")]
    # What moved with it, in the units each is measured in, largest share of the slide first.
    lids, iris = drift.get("lids_mm_s"), drift.get("iris_per_s")
    pitch, distance = drift.get("pitch_deg_s"), drift.get("distance_mm_s")
    causes = []
    if lids is not None and abs(lids) >= 0.01:
        causes.append(f"the lids {'closed' if lids < 0 else 'opened'} {abs(lids) * 60:.1f} mm a minute")
    if iris is not None and abs(iris) >= 0.0005:
        causes.append(f"the irises read {'lower' if iris > 0 else 'higher'} in the eye")
    if pitch is not None and abs(pitch) >= 0.02:
        causes.append(f"the head's tilt changed {abs(pitch) * 60:.1f} degrees a minute")
    if distance is not None and abs(distance) >= 0.5:
        causes.append(f"the head moved {'away' if distance > 0 else 'closer'} {abs(distance) * 60:.0f} mm a minute")
    why = "; meanwhile " + ", ".join(causes) if causes else "; nothing measured moved with it"
    return [(INFO, "stare drift", detail + why)]


def _screen(status: dict) -> list[tuple[str, str, str]]:
    """Reading the window under the gaze in other apps (screen.py)."""
    screen = status.get("screen")
    if screen is None:
        return [(WARN, "screen reading", "the receiver is older than this checkout: " + RESTART)]
    if not screen.get("running"):
        why = screen.get("reason") or screen.get("error") or "stopped"
        if "--no-screen" in why or "needs Windows" in why:
            return [(INFO, "screen reading", f"off ({why})")]
        return [(
            WARN,
            "screen reading",
            f"not running: {why}. Install its parts with uv sync --project services/capture-win, then " + RESTART,
        )]  # fmt: skip
    if not screen.get("enabled"):
        return [(INFO, "screen reading", "off: python -m capture screen on turns it on")]
    if screen.get("error"):
        return [(WARN, "screen reading", f"failing: {screen['error']}")]
    reads, apps, ms = screen.get("reads_10min", 0), screen.get("apps_10min") or [], screen.get("ocr_ms")
    now = screen.get("reading")
    language = f" ({screen['language']})" if screen.get("language") else ""
    detail = f"{screen.get('engine')} OCR{language}, {reads} reads in 10 min" + (
        f" of {', '.join(apps)}" if apps else ""
    )
    detail += f", {ms:.0f} ms each" if ms is not None else ""
    if now:
        detail += f"; reading {now['app']} now"
    elif screen.get("skipped") and screen["skipped"] != "no gaze":
        detail += f"; not reading now: {screen['skipped']}"
    return [(PASS, "screen reading", detail)]


def _tuning(vault: Path) -> list[tuple[str, str, str]]:
    """The last tuning of learning to your clicks (services/gaze/gaze/tune.py), from its file."""
    try:
        tuned = json.loads((vault / "calibration" / "gaze-tuned.json").read_text(encoding="utf-8"))
        held = tuned["replay"]["held_out_px"]
        detail = (
            f"{tuned['t'][:10]}: {tuned['clicks']} clicks replayed; on the newest "
            f"{tuned['replay']['held_out_clicks']}, {held['in_use']} px with the settings in use, {held['best']} px "
            "with the best found: "
        )
    except (OSError, ValueError, KeyError, TypeError):
        return []
    changed = tuned.get("changed") or {}
    if tuned.get("accepted"):
        return [
            (PASS, "tuning", detail + "taken (" + ", ".join(f"{k} {a} -> {b}" for k, (a, b) in changed.items()) + ")")
        ]
    return [(INFO, "tuning", detail + "kept the settings in use (not 3% better)")]


def _jobs(vault: Path, now: float | None = None) -> list[tuple[str, str, str]]:
    """The vault's jobs (capture.jobs): what runs, what waits and why, what failed."""
    try:
        from ingest.jobs.stages import STAGES
        from ingest.jobs.store import Jobs
    except ImportError:
        return []
    if not (vault / "jobs.sqlite").is_file():
        return []
    jobs = Jobs.of(vault)
    try:
        state = jobs.status(now=now)
    finally:
        jobs.close()
    now = time.time() if now is None else now

    def what(job: dict) -> str:
        inputs = job["inputs"]
        return "/".join(str(inputs[k]) for k in ("date", "moment") if k in inputs) or json.dumps(inputs)

    def title(name: str) -> str:
        stage = STAGES.get(name)
        return (stage.title if stage and stage.title else name).capitalize()

    rows = []
    for job in state["running"]:
        minutes = (now - (job.get("started") or now)) / 60
        rows.append((INFO, "jobs", f"{title(job['stage'])} of {what(job)}: running {minutes:.0f} min ({job['worker']})"))
    for name, counts in sorted(state["stages"].items()):
        if not counts["queued"]:
            continue
        stage = STAGES.get(name)
        if stage is not None and not stage.ready():
            when = f"once it is installed: {stage.install or 'see docs/jobs.md'}"
        elif stage is not None and stage.run is None and name in ("speech", "conversations") and speech_python() is None:
            rows.append((WARN, "jobs", f"{title(name)}: {counts['queued']} waiting, and the speech worker is not"
                         " installed: uv sync --project services/speech (docs/speech.md)"))  # fmt: skip
            continue
        elif stage is not None and stage.run is None:
            when = f"by {stage.worker or 'its worker'}, when you are away 10 min or at night"
        elif stage is not None and stage.needs == "gpu":
            when = "when you are away 10 min, or at night"
        else:
            when = "next"
        rows.append((INFO, "jobs", f"{title(name)}: {counts['queued']} waiting, {when}"))
    for job in state["failed"]:
        reason = (job.get("error") or "").strip().splitlines()[-1:] or ["no reason kept"]
        rows.append((WARN, "jobs", f"{title(job['stage'])} of {what(job)} failed: {reason[0][:160]}"
                     f" (uv run daycube jobs retry --stage {job['stage']})"))  # fmt: skip
    if not rows:
        done = sum(c["done"] for c in state["stages"].values())
        rows.append((PASS, "jobs", f"nothing waiting; {done} done"))
    return rows


def _speech_check(vault: Path) -> list[tuple[str, str, str]]:
    """The speech models' last check (``python -m speech canary``, derived/speech/canary.json)."""
    try:
        report = json.loads((vault / "derived" / "speech" / "canary.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    when = str(report.get("made", ""))[:16].replace("T", " ")
    where = f"{report.get('asr', 'the models')} on {report.get('device', '?')}, {when} UTC"
    if report.get("ok"):
        return [(PASS, "speech check", f"{where}: zh CER {report.get('zh_cer')}, en WER {report.get('en_wer')}")]
    why = report.get("why") or f"zh CER {report.get('zh_cer')}, en WER {report.get('en_wer')}, speakers {report.get('speakers_ok')}"
    return [(FAIL, "speech check", f"{where}: {why} (docs/speech.md, the check)")]


PHONE_QUIET_S = 2 * 3600  # the app sends a heartbeat every hour


def _phones(vault: Path, now: float | None = None) -> list[tuple[str, str, str]]:
    """Paired phones (capture.phone_api): when each was last heard from, its battery and queue. A
    phone quiet for two hours is recording nothing the vault will get (the app was swiped away,
    the phone is off or offline)."""
    path = vault / "capture" / "phone-devices.json"
    try:
        devices = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    now = time.time() if now is None else now
    rows = []
    for device, info in sorted(devices.items()):
        name = info.get("name") or device
        last = info.get("last")
        if not last:
            rows.append((INFO, "phone", f"{name}: paired, nothing heard from it yet"))
            continue
        age = now - float(last.get("received", 0))
        parts = []
        if last.get("battery") is not None:
            parts.append(f"battery {round(100 * last['battery'])}%" + (" charging" if last.get("charging") else ""))
        if last.get("queue_files"):
            parts.append(f"{last['queue_files']} files waiting to upload")
        recording = [k for k, v in (last.get("state") or {}).items() if v and v != "off"]
        if recording:
            parts.append("recording " + ", ".join(recording))
        heard = f"{age / 60:.0f} min ago" if age < 3600 else f"{age / 3600:.0f} h ago"
        level = PASS if age < PHONE_QUIET_S else WARN
        hint = "" if level == PASS else "; open DayCube Capture on the phone (never swipe it away)"
        rows.append((level, "phone", f"{name}: heard {heard}" + (f", {', '.join(parts)}" if parts else "") + hint))
    return rows


def checks(status: dict | None, vault: Path, zone: ZoneInfo, now: datetime, autostart: str | None) -> list[tuple]:
    out: list[tuple[str, str, str]] = []
    if status is None:
        return [(FAIL, "receiver", "not running on 127.0.0.1:7700: start it, or sign out and in (autostart)")]
    out.append((WARN if status["paused"] else PASS, "receiver", "paused" if status["paused"] else "running"))
    if status.get("code") != code_stamp():  # it runs the code it started with, not the checkout's
        out.append((FAIL, "receiver code", "older than this checkout (started before the last update): " + RESTART))

    age = _age(status.get("browser", {}).get("last"), now)
    if age is None:
        out.append((WARN, "browser", "no records since the receiver started: is the extension on and Opera open?"))
    else:
        out.append((PASS if age < 600 else INFO, "browser", f"last record {age / 60:.0f} min ago"))

    gaze = status.get("gaze", {})
    if not gaze.get("tracking"):
        reason = gaze.get("reason") or "unknown"
        normal = reason in ("paused", "away", "locked", "camera busy")
        detail = f"not tracking: {reason}"
        if reason == "paused":
            detail += " (the extension's toolbar button or Alt+Shift+D resumes)"
        elif not normal:
            last = tracker_log(vault)
            if last:
                detail += f". Last from the tracker's logs: {last}"
        out.append((INFO if normal else FAIL, "eye tracker", detail))
    else:
        face_seen = (gaze.get("face") or 0) >= 0.8
        if gaze.get("fps") and face_seen and gaze.get("coverage") is None and gaze.get("noise_px") is None:
            # A current tracker reports both within seconds of turning the camera on, given a face
            # (with none in view there are no fixations to measure: 7% of frames, 2026-10-25).
            out.append((WARN, "tracker code", "older than this checkout: restarting the receiver restarts it"))
        fps, target = gaze.get("fps"), gaze.get("target_fps") or 30
        out.append((PASS if fps and fps >= 0.8 * target else WARN, "frame rate", f"{fps} of {target} frames/s"))
        camera_fps, mode = gaze.get("camera_fps"), gaze.get("camera_mode")
        if camera_fps is not None:
            if camera_fps >= 50:
                out.append((PASS, "webcam", f"{camera_fps:.0f} frames/s in {mode}"))
            else:
                hint = (
                    ": calibrating again (uv run --project services/gaze python -m gaze calibrate) tries its 60 fps modes"
                    if mode == "default mode"
                    else ""
                )
                out.append(
                    (INFO, "webcam", f"{camera_fps:.0f} frames/s in {mode}, so {camera_fps:.0f} gaze points/s{hint}")
                )
        face = gaze.get("face")
        verdict = PASS if face is not None and face >= 0.8 else WARN if face is not None and face >= 0.5 else FAIL
        out.append((verdict, "face found", "unknown" if face is None else f"{face:.0%} of frames"))
        timing = gaze.get("timing_ms")
        if timing and timing.get("frame") is not None:
            budget = 1000 / (gaze.get("target_fps") or 30)
            parts = [
                f"face landmarks {timing.get('landmarks', 0):.1f}",
                f"gaze model {timing.get('model', 0):.1f}",
                f"fixations {timing.get('tracking', 0):.1f}",
                f"network {timing.get('network', 0):.1f}",
            ]
            if timing.get("live_post") is not None:
                parts.append(f"live point {timing['live_post']:.1f} on its own thread")
            out.append((
                PASS if timing["frame"] <= budget else WARN,
                "frame time",
                f"{timing['frame']:.1f} ms of {budget:.0f}: " + ", ".join(parts),
            ))  # fmt: skip
        coverage = gaze.get("coverage")
        if coverage is not None:
            verdict = PASS if coverage >= 0.6 else WARN if coverage >= 0.3 else FAIL
            noise = gaze.get("noise_px")
            out.append((verdict, "fixations", f"{coverage:.0%} of gaze" + (f", noise {noise:.0f} px" if noise else "")))

    display = status.get("display")
    overlay = gaze.get("overlay") or {}
    native = bool(display and display.get("native"))
    if display is not None and not display.get("gaze_ring"):
        out.append((
            INFO,
            "gaze ring",
            "off: turn on \"Show where my eyes are\" in the extension's options, or run python -m capture ring on",
        ))  # fmt: skip
    elif display is not None and gaze.get("tracking"):
        if native and overlay.get("shown") and overlay.get("fps") is not None:
            refresh = overlay.get("refresh_hz")
            fps, moves = overlay["fps"], overlay.get("moves_per_s") or 0
            verdict = PASS if refresh is None or fps >= 0.9 * min(refresh, 60) else WARN
            out.append((
                verdict,
                "gaze ring",
                f"drawn over every window: {fps:.0f} frames/s"
                + (f" on a {refresh:.0f} Hz display" if refresh else "")
                + f", moving {moves:.0f} times/s between {gaze.get('fps') or 0:.0f} gaze points/s",
            ))  # fmt: skip
        elif native:
            out.append((INFO, "gaze ring", "drawn over every window; hidden now (no gaze in the last 4 s)"))
        elif overlay.get("error"):
            out.append((
                WARN,
                "gaze ring",
                f"the window over the screen could not open ({overlay['error']}); pages draw the ring instead",
            ))  # fmt: skip
        elif not overlay:
            out.append((
                WARN,
                "gaze ring",
                "the tracker is older than this checkout and draws no ring of its own: " + RESTART,
            ))  # fmt: skip

    ring = status.get("ring")
    if gaze.get("tracking") and ring is not None and not native:
        points, asks, share = ring.get("points_per_s", 0), ring.get("asks_per_s", 0), ring.get("live_share")
        if not ring.get("ever_asked"):
            out.append((
                INFO,
                "gaze ring",
                "no page has asked for the live gaze point. If the ring is on and jumps about once a second, "
                "the extension is older than 0.7.0: reload it at opera://extensions",
            ))  # fmt: skip
        elif asks and points < 3:
            out.append((
                WARN,
                "gaze ring",
                f"the page asks {asks:.0f} times/s but the tracker sends {points:.0f} live points/s, so the ring "
                "falls back to fixations (about one a second): restart the receiver (" + RESTART + ")",
            ))  # fmt: skip
        elif asks and share is not None and share < 0.5:
            out.append((
                WARN,
                "gaze ring",
                f"live points arrive ({points:.0f}/s) but only {share:.0%} land on the page's window: "
                "is the page on the calibrated monitor?",
            ))  # fmt: skip
        elif asks:
            out.append((PASS, "gaze ring", f"{points:.0f} live points/s, the page asks {asks:.0f} times/s"))

    out += _screen(status)

    saved = _calibration(vault)
    model = (saved or {}).get("model") or gaze.get("model")
    if model and model.startswith("head"):
        moved, turned = gaze.get("head_moved_mm"), gaze.get("head_turned_deg")
        where = (
            f"; head now {moved / 10:.0f} cm and {turned:.0f} degrees from where it was at calibration"
            if moved is not None and turned is not None
            else ""
        )
        kind = "head-aware (video landmarks)" if model == "head-video" else "head-aware"
        out.append((PASS, "gaze model", f"{kind}: follows head movement" + where))
    elif model:
        out.append((
            WARN,
            "gaze model",
            f"EyeTrax {model}: does not follow head movement. Calibrating "
            "(uv run --project services/gaze python -m gaze calibrate) compares it with the head-aware model",
        ))  # fmt: skip

    # The saved calibration is the one that counts: the receiver's copy of the tracker's state can
    # be from a tracker that has stopped, or from before a recalibration.
    monitor = (saved or {}).get("monitor") or gaze.get("monitor")
    limit = USABLE_PX_AT_1920 * (monitor[0] / 1920 if monitor else 1.0)
    error = (saved or {}).get("error_px", gaze.get("error_px"))
    if error is not None:
        verdict = PASS if error <= limit else FAIL
        when = f", saved {saved['t'][:16].replace('T', ' ')} UTC" if saved and saved.get("t") else ""
        out.append((verdict, "calibration", f"{error:.0f} px median held-out error (limit {limit:.0f}){when}"))
        running = gaze.get("error_px")
        if gaze.get("tracking") and saved and running is not None and abs(running - error) > 0.5:
            out.append((WARN, "calibration", "the tracker still uses the calibration from before: " + RESTART))

    out += _learning(gaze)
    out += _drift(gaze)
    if gaze.get("tracking") and gaze.get("learning"):
        out += _tuning(vault)
    out += _jobs(vault)
    out += _speech_check(vault)
    out += _phones(vault)

    decoding = status.get("line_decoding") or {}
    seen = decoding.get("fixations", 0)
    if gaze.get("tracking") and seen >= 30:
        on_page, on_line, page = decoding.get("on_page", 0), decoding.get("on_line", 0), decoding.get("page")
        reasons = {
            "outside": "outside every page and app window being read (or the calibration is off)",
            "no_page": "with no browser page on screen (reload the extension, then the tab)",
            "stale": "while the page was not reporting (reload the extension, then the tab)",
            "other_monitor": "with the page on another monitor (gaze is calibrated on the primary one)",
        }
        missed = sorted(((decoding.get(k, 0), why) for k, why in reasons.items() if decoding.get(k, 0)), reverse=True)
        detail = f"last {seen} fixations: {on_page} on a page, {on_line} on a line"
        if page:
            detail += f"; {page['host']}: {page['with_lines']} of {page['blocks']} text blocks with lines"
        if on_page < 0.2 * seen:
            why = f"; {missed[0][0]} {missed[0][1]}" if missed else ""
            verdict, detail = WARN, detail + ": gaze is not landing on pages" + why
        elif page and page["blocks"] and not page["with_lines"]:
            verdict, detail = WARN, detail + ": the page sent no lines (reload the extension, then the tab)"
        elif on_line < 0.3 * on_page:
            verdict = WARN
        else:
            verdict = PASS
        out.append((verdict, "line reading", detail))

    accuracy = status.get("accuracy") or {}
    clicks, corrected = accuracy.get("clicks", 0), accuracy.get("corrected_error_px")
    if clicks < 5 or corrected is None:
        learned = (gaze.get("learning") or {}).get("version")
        if learned:  # the page-click offsets start over at each model learned from clicks
            out.append((
                INFO,
                "live accuracy",
                f"{clicks} of 5 page clicks since the last learned update; the learning line has the accuracy",
            ))  # fmt: skip
        else:
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
        for line in (day / "attention.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(ValueError):
                dwells += json.loads(line).get("phase") == "start"
    out.append((INFO, "dwells today", str(dwells)))
    reads = lines_read = 0
    if (day / "reading.jsonl").is_file():
        for line in (day / "reading.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
            with contextlib.suppress(ValueError, AttributeError, TypeError):
                lines_read += len(json.loads(line).get("lines") or [])
                reads += 1
    out.append((INFO, "reading today", f"{reads} blocks read, {lines_read} lines"))

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
        # trust_env=False: a system proxy (Windows takes it from the registry) must not see localhost.
        status = httpx.get(f"http://127.0.0.1:{port}/status", timeout=2, trust_env=False).json()
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

"""``python -m gaze``: calibrate, track, or check the webcam."""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import calibrate as calibration
from .calibrate import usable_limit
from .track import DEFAULT_FPS

NOT_CALIBRATED = 3  # the capture service's supervisor waits for a new calibration on this code
OTHER_MONITOR = 4  # ... and retries every minute on this one (a laptop undocked, a monitor off)
DEFAULT_VAULT = Path(os.environ.get("DAYCUBE_VAULT_ROOT") or Path(__file__).resolve().parents[3] / "vault")


def _logging(vault: Path) -> None:
    for name in ("stdout", "stderr"):  # pythonw has neither
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
    (vault / "logs").mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(vault / "logs" / "gaze.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def do_calibrate(args: argparse.Namespace) -> None:
    from .receiver import Receiver

    # A running tracker holds the webcam: pause recording (which releases it) while calibrating.
    receiver = Receiver(args.receiver, args.vault / ".token")
    pause = receiver.paused() is False and receiver.control("pause")
    if pause:
        print("Recording is paused while you calibrate.")
        time.sleep(2)
    print("A full-screen window opens. Keep your head still and follow each green dot with your eyes.")
    print("After 13 dots it asks you to move your head; then 4 more dots. Esc cancels. About 45 seconds.")
    try:
        result = calibration.calibrate(args.vault, camera=args.camera, model=args.model)
    except calibration.Aborted:
        raise SystemExit("Calibration cancelled.") from None
    finally:
        if pause:
            receiver.control("resume")
    print(report(result))
    if not result["usable"]:
        print(
            "Too coarse to tell paragraphs apart. Try again with your face evenly lit, the camera at eye level, "
            "and about an arm's length from the screen."
        )


def report(result: dict) -> str:
    """The calibration's scores per candidate model, the chosen one marked."""
    width = result["monitor"][0]
    lines = [
        "Median error at the held-out dots, monitor pixels, after the tracker's smoothing (raw per frame):",
        f"  {'model':<30} {'head still':>14} {'head moved':>14}",
    ]
    for name, row in result.get("candidates", {}).items():

        def cell(key: str, row: dict = row) -> str:
            value, raw = row.get(f"{key}_px"), row.get(f"{key}_raw_px")
            return "-" if value is None else f"{value:.0f} ({raw:.0f})"

        mark = "  <- using" if name == result["model"] else ""
        label = calibration.LABELS.get(name, f"EyeTrax {name}")
        lines.append(f"  {label:<30} {cell('still'):>14} {cell('moved'):>14}{mark}")
    moved = result.get("head_moved_mm")
    if moved is not None:
        note = "" if moved >= 30 else ": too little to test head movement; move more next time"
        lines.append(f"Your head moved {moved / 10:.0f} cm for the moved-head dots{note}.")
    if result.get("mirrored_camera"):
        lines.append("The webcam image is mirrored; the tracker flips it back.")
    lines.append(
        f"Using {calibration.LABELS.get(result['model'], result['model'])}: {result['error_px']:.0f} px with the head "
        f"still (usable up to {usable_limit(width):.0f} px on this {width}-px-wide monitor)."
    )
    return "\n".join(lines)


def do_track(args: argparse.Namespace) -> None:
    _logging(args.vault)
    log = logging.getLogger("gaze")
    result = calibration.load(args.vault)
    if result is None:
        log.error("not calibrated: run `python -m gaze calibrate`")
        raise SystemExit(NOT_CALIBRATED)
    # The limit is applied now rather than read from the file, so a calibration saved under an
    # older, looser limit is judged by the current one.
    if result["error_px"] > usable_limit(result["monitor"][0]) and not args.force:
        log.error("calibration error %.0f px is too coarse; recalibrate (or pass --force)", result["error_px"])
        raise SystemExit(NOT_CALIBRATED)
    from eyetrax.utils.video import open_camera

    from .receiver import Receiver
    from .system import primary_monitor
    from .track import Tracker

    if list(primary_monitor()) != result["monitor"]:
        log.error("the primary monitor is now %s, calibrated on %s", primary_monitor(), result["monitor"])
        raise SystemExit(OTHER_MONITOR)
    estimator = calibration.estimator_for(result["model"], tuple(result["monitor"]), result.get("mm_per_px"))
    estimator.load_model(args.vault / "calibration" / result["model_file"])
    if not result["model"].startswith("head"):
        log.warning("running EyeTrax %s, which does not follow head movement", result["model"])
    receiver = Receiver(args.receiver, args.vault / ".token")
    camera = args.camera if args.camera is not None else result["camera"]
    calibrated = (args.vault / "calibration" / calibration.CALIBRATION_FILE).stat().st_mtime_ns

    def recalibrated() -> bool:
        path = args.vault / "calibration" / calibration.CALIBRATION_FILE
        return not path.is_file() or path.stat().st_mtime_ns != calibrated

    tracker = Tracker(
        estimator,
        lambda: open_camera(camera),
        receiver,
        result,
        fps=args.fps,
        supervised=args.supervised,
        recalibrated=recalibrated,
    )
    log.info("tracking with camera %s at up to %s fps", camera, args.fps)
    try:
        tracker.run()
    except KeyboardInterrupt:
        pass
    finally:
        estimator.close()
        log.info("tracking stopped")


def do_check(args: argparse.Namespace) -> None:
    """Open the camera and report frame rate and how often a face is found, without recording."""
    from eyetrax import GazeEstimator
    from eyetrax.utils.video import open_camera

    estimator = GazeEstimator()
    capture = open_camera(args.camera)
    frames = faces = 0
    started = time.monotonic()
    try:
        while time.monotonic() - started < args.seconds:
            ok, frame = capture.read()
            if not ok:
                continue
            frames += 1
            faces += estimator.extract_features(frame)[0] is not None
    finally:
        capture.release()
        estimator.close()
    elapsed = time.monotonic() - started
    print(
        f"camera {args.camera}: {frames / elapsed:.1f} frames/s, face found in {faces / max(frames, 1):.0%} of frames"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m gaze", description="Webcam eye tracking for DayCube")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--vault", type=Path, default=DEFAULT_VAULT, help="The DayCube vault (default: the repository's)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    cal = commands.add_parser("calibrate", parents=[common], help="Learn where you look: 13 targets, about 30 s")
    cal.add_argument("--camera", type=int, default=0)
    cal.add_argument(
        "--model",
        default=calibration.AUTO,
        help="auto (default: the best of head, head-video and ridge on your own frames), one of those, "
        "or another EyeTrax model: elastic_net, svr, tiny_mlp",
    )
    cal.add_argument("--receiver", default="http://127.0.0.1:7700")
    cal.set_defaults(handler=do_calibrate)

    track = commands.add_parser("track", parents=[common], help="Send fixations to the capture receiver until stopped")
    track.add_argument("--camera", type=int, help="Default: the calibrated camera")
    track.add_argument("--receiver", default="http://127.0.0.1:7700")
    track.add_argument("--fps", type=float, default=DEFAULT_FPS, help="Processing target in frames/s (default: 30)")
    track.add_argument("--force", action="store_true", help="Track even with a coarse calibration")
    track.add_argument("--supervised", action="store_true", help=argparse.SUPPRESS)
    track.set_defaults(handler=do_track)

    check = commands.add_parser("check", parents=[common], help="Test the webcam: frame rate and face detection")
    check.add_argument("--camera", type=int, default=0)
    check.add_argument("--seconds", type=float, default=10.0)
    check.set_defaults(handler=do_check)

    args = parser.parse_args(argv)
    if args.command == "track" and (not math.isfinite(args.fps) or args.fps <= 0):
        parser.error("--fps must be positive and finite")
    args.vault = args.vault.resolve()
    args.handler(args)


if __name__ == "__main__":
    main()

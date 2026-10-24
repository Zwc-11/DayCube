"""``python -m capture``: the receiver, its autostart entry, a health check and the Opera history import."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import sys
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from ingest.config import Settings

PORT = 7700  # the extension's host permission names this port


def _logging(vault_root: Path) -> None:
    # Under pythonw (autostart) there is no console: stdout and stderr are None, which breaks
    # libraries that print. Everything goes to vault/logs/capture.log instead.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
    (vault_root / "logs").mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(vault_root / "logs" / "capture.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def _split(command: str | None) -> list[str] | None:
    """A command line as arguments. Windows paths keep their backslashes; quotes only group."""
    if not command:
        return None
    if os.name != "nt":
        return shlex.split(command)
    return [p[1:-1] if len(p) > 1 and p[0] == p[-1] == '"' else p for p in shlex.split(command, posix=False)]


def run(args: argparse.Namespace) -> None:
    import uvicorn

    from .api import create_capture_app
    from .attention import Attention
    from .supervise import GazeSupervisor, gaze_python

    settings = Settings()
    _logging(settings.vault_root)
    log = logging.getLogger("capture")
    attention = Attention(threshold_s=args.dwell_seconds, factor=args.dwell_factor)
    app = create_capture_app(settings, receive=args.receive, attention=attention, on_dwell=_split(args.on_dwell))
    supervisor = None
    if args.gaze:
        python = gaze_python()
        if python is None:
            log.warning("eye tracker not installed: run `uv sync --project services/gaze`")
        else:
            supervisor = GazeSupervisor(python, settings.vault_root, f"http://127.0.0.1:{args.port}")
    log.info(
        "receiver starting on 127.0.0.1:%s (%s)", args.port, "paused" if app.state.receiver.paused else "receiving"
    )
    if supervisor is not None:
        supervisor.start()
    librarian = None
    if args.receive and not args.no_notes:
        from .librarian import Librarian

        librarian = Librarian(settings.vault_root, settings.timezone)
        librarian.start()
    receiver = app.state.receiver
    if args.no_screen:
        receiver.screen_problem = "started with --no-screen"
    else:
        from .screen import start as read_screen

        receiver.screen, receiver.screen_problem = read_screen(receiver, screen_file(settings.vault_root))
        if receiver.screen is None:
            log.warning("not reading the screen: %s", receiver.screen_problem)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_config=None, access_log=False)
    finally:
        if supervisor is not None:
            supervisor.stop()
        if librarian is not None:
            librarian.stop()
        if receiver.screen is not None:
            receiver.screen.stop()
        log.info("receiver stopped")


def screen_file(vault_root: Path) -> Path:
    return vault_root / "capture" / "screen.json"


def screen(args: argparse.Namespace) -> None:
    """Turn reading the window under the gaze (any app) on or off, or exclude an app from it. A
    running receiver picks the change up within a second."""
    import json

    settings = Settings()
    if args.state == "check":
        check_screen()
        return
    path = screen_file(settings.vault_root)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        current = current if isinstance(current, dict) else {}
    except (OSError, ValueError):
        current = {}
    excluded = [str(app).casefold() for app in current.get("exclude_apps", []) if isinstance(app, str)]
    if args.state in ("on", "off"):
        current["enabled"] = args.state == "on"
    for app in args.exclude or []:
        app = app.casefold() if app.casefold().endswith(".exe") else app.casefold() + ".exe"
        excluded = sorted({*excluded, app})
    for app in args.include or []:
        app = app.casefold() if app.casefold().endswith(".exe") else app.casefold() + ".exe"
        excluded = [a for a in excluded if a != app]
    current["exclude_apps"] = excluded
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=1) + "\n", encoding="utf-8")
    state = "on" if current.get("enabled", True) else "off"
    print(
        f"Reading the window under your gaze: {state}." + (f" Never read: {', '.join(excluded)}." if excluded else "")
    )


def check_screen() -> None:
    """Read the window in front once, as the receiver would, and print what was found."""
    import time

    from .screen import Win32System, blocks_of, engine, why_not

    system = Win32System()
    print("Reading the window in front in 3 seconds: bring the one to read to the front.")
    time.sleep(3)
    window = system.foreground()
    if window is None:
        raise SystemExit("No window in front.")
    print(f"{window.app}: {window.title!r}, {window.rect[2]}x{window.rect[3]} at {window.rect[0]},{window.rect[1]}")
    reason = why_not(window)
    if reason is not None:
        print(f"The receiver never reads this window ({reason}); reading it anyway for this check.")
    ocr = engine()
    started = time.perf_counter()
    lines = ocr.read(system.capture(window.rect))
    took = (time.perf_counter() - started) * 1000
    blocks = blocks_of(lines)
    language = getattr(ocr, "language", "")
    print(f"{ocr.name} OCR {language}: {len(lines)} lines in {len(blocks)} paragraphs, {took:.0f} ms")
    for block in blocks[:12]:
        print(f"  [{block['tag']}] {block['text'][:110]}")


def install_autostart(args: argparse.Namespace) -> None:
    from . import autostart

    command = autostart.launch_command(gaze=not args.no_gaze, on_dwell=args.on_dwell)
    print(f"Starts at every sign-in:\n  {autostart.install(command)}")
    try:
        httpx.get(f"http://127.0.0.1:{PORT}/status", timeout=1)
        print("The receiver is already running; the new entry applies from the next sign-in.")
    except httpx.HTTPError:
        autostart.start_detached(command)
        print("Started it now. Logs: vault/logs/capture.log")


def uninstall_autostart(_: argparse.Namespace) -> None:
    from . import autostart

    print("Removed the sign-in entry." if autostart.uninstall() else "There was no sign-in entry.")
    print("A receiver that is running keeps running until you sign out or stop it in Task Manager.")


def autostart_status(_: argparse.Namespace) -> None:
    from . import autostart

    line = autostart.current()
    print(f"Starts at sign-in:\n  {line}" if line else "Not set to start at sign-in.")


def doctor(_: argparse.Namespace) -> None:
    from .doctor import main as check

    settings = Settings()
    raise SystemExit(check(settings.vault_root, settings.timezone, PORT))


def ring(args: argparse.Namespace) -> None:
    """Show or hide the gaze ring over every window: tells a running receiver, else remembers it
    for the next start."""
    import json
    from urllib.error import URLError
    from urllib.request import ProxyHandler, Request, build_opener

    from ingest.vault import Vault

    settings = Settings()
    vault = Vault(settings.vault_root)
    vault.initialize()
    on = args.state == "on"
    request = Request(
        f"http://127.0.0.1:{PORT}/display",
        data=json.dumps({"gaze_ring": on}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {vault.token}"},
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
            response.read()
        print(f"Gaze ring {args.state}.")
    except (URLError, OSError):
        path = vault.root / "capture" / "display.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        path.write_text(json.dumps({**current, "gaze_ring": on}) + "\n", encoding="utf-8")
        print(f"The receiver is not running; the gaze ring will be {args.state} when it starts.")


def import_history(args: argparse.Namespace) -> None:
    from ingest.vault import Vault

    from .history import Exclusions, import_history, opera_histories

    settings = Settings()
    sources = [args.profile] if args.profile else opera_histories()
    if not sources:
        raise SystemExit(
            "No Opera History file found under %APPDATA%\\Opera Software; pass --profile <path to History>."
        )
    vault = Vault(settings.vault_root)
    vault.initialize()
    exclusions = Exclusions.for_vault(vault, args.exclude or ())
    since = date.fromisoformat(args.since) if args.since else None
    days = import_history(vault, ZoneInfo(settings.timezone), sources, since=since, exclusions=exclusions)
    print(f"From {', '.join(map(str, sources))}:")
    print(f"  {sum(days.values())} visits on {sum(1 for n in days.values() if n)} days", end="")
    removed = [day for day, n in days.items() if not n]
    print(f"; removed {len(removed)} days with no visits left" if removed else "")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m capture", description="DayCube capture on this computer")
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("run", help="Start the receiver (the default command)")
    start.add_argument("--receive", action="store_true", help="Receive from the start unless a pause is remembered")
    start.add_argument("--gaze", action="store_true", help="Also run the eye tracker from services/gaze")
    start.add_argument(
        "--no-notes", action="store_true", help="Do not keep the day's search index and linked notes current"
    )
    start.add_argument("--dwell-seconds", type=float, default=8.0, help="Shortest fixation time that counts as a dwell")
    start.add_argument(
        "--dwell-factor", type=float, default=2.0, help="A dwell also needs this many times the text's reading time"
    )
    start.add_argument("--on-dwell", help="Command run on each dwell, with the dwell record as JSON on stdin")
    start.add_argument(
        "--no-screen", action="store_true", help="Do not read the window under the gaze in apps other than the browser"
    )
    start.add_argument("--port", type=int, default=PORT, help=argparse.SUPPRESS)
    start.set_defaults(handler=run)

    auto = commands.add_parser("install-autostart", help="Start receiver and eye tracker at every Windows sign-in")
    auto.add_argument("--no-gaze", action="store_true", help="Leave the eye tracker out")
    auto.add_argument("--on-dwell", help="Command run on each dwell (see run --on-dwell)")
    auto.set_defaults(handler=install_autostart)
    commands.add_parser("uninstall-autostart", help="Remove the sign-in entry").set_defaults(
        handler=uninstall_autostart
    )
    commands.add_parser("autostart-status", help="Show the sign-in entry").set_defaults(handler=autostart_status)

    commands.add_parser(
        "doctor", help="Check capture from what it recorded: receiver, extension, eye tracker, accuracy, autostart"
    ).set_defaults(handler=doctor)

    show = commands.add_parser("ring", help="Show or hide the gaze ring drawn over every window")
    show.add_argument("state", choices=["on", "off"])
    show.set_defaults(handler=ring)

    read = commands.add_parser(
        "screen", help="Turn reading the window under your gaze (any app) on or off, or never read an app"
    )
    read.add_argument(
        "state", nargs="?", choices=["on", "off", "check"], help="check: read the window in front once and print it"
    )
    read.add_argument("--exclude", action="append", metavar="APP", help="Never read this program (slack.exe)")
    read.add_argument("--include", action="append", metavar="APP", help="Read this program again")
    read.set_defaults(handler=screen)

    history = commands.add_parser("import-history", help="Copy Opera's history into vault/capture/<date>/history.jsonl")
    history.add_argument("--profile", type=Path, help="A Chromium History file (default: Opera's)")
    history.add_argument("--since", help="First day to import, YYYY-MM-DD (default: all that Opera kept)")
    history.add_argument("--exclude", action="append", help="Leave out a domain and its subdomains (repeatable)")
    history.set_defaults(handler=import_history)

    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["run", *argv]  # `python -m capture --receive` as before
    args = parser.parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()

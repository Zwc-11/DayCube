"""``daycube``: the vault's command line. Each command is one function that returns what is
printed as JSON; ``build_parser`` lists them with their arguments."""

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .index.embed import default_embedder
from .store import LifeStore
from .vault import Vault

Command = Callable[..., Any]  # (args, vault, settings); ``mcp`` alone takes (args, settings)


def say(line: str) -> None:
    """Progress goes to stderr, so stdout stays one JSON document."""
    print(line, file=sys.stderr, flush=True)


def search_store(vault: Vault, settings: Settings) -> LifeStore:
    return LifeStore(vault, default_embedder(settings.vector_search, settings.embedding_model), zone=settings.timezone)


def init(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    return {"vault": str(vault.root), "token_file": str(vault.root / ".token")}


def seed(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    try:
        result = {"date": vault.seed(), "seeded": True}
    except FileExistsError as error:
        result = {"seeded": False, "message": str(error)}
    if args.capture:
        from .capture.sample import SAMPLE_CAPTURE_DAY, write_sample_capture

        if (vault.root / "capture" / SAMPLE_CAPTURE_DAY).exists():
            result["capture"] = {"seeded": False, "message": f"Capture for {SAMPLE_CAPTURE_DAY} already exists"}
        else:
            write_sample_capture(vault, damaged=False)
            result["capture"] = {"date": SAMPLE_CAPTURE_DAY, "seeded": True}
    return result


def doctor(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    import importlib.util
    import shutil
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE VIRTUAL TABLE test USING fts5(text)")
    return {
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "fts5": True,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "vault": str(vault.root),
        "days": len(vault.list_days()),
        "optional_libraries": {name: importlib.util.find_spec(name) is not None for name in ("anthropic", "fastembed")},
    }


def validate(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    return vault.validate(args.date)


def stats(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    return search_store(vault, settings).stats()


def graph(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    return search_store(vault, settings).graph(limit=100)


def data(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    from .datacenter import report

    return report(search_store(vault, settings))


def index(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    store = search_store(vault, settings)
    return [store.update(day) for day in store.days()] if args.all else store.update(args.date)


def notes(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    from .notes import Notes

    store = search_store(vault, settings)
    writer = Notes(store)
    days = store.days() if args.all else [args.date]
    paths = sorted({p.relative_to(writer.root).as_posix() for day in days for p in writer.write_day(day)})
    return {"folder": str(writer.root), "notes": paths}


def timeline(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    from .capture import capture_days
    from .review import has_inputs, write
    from .review.labels import llm_labeler

    labeler, llm = llm_labeler(settings.category_llm)
    if args.all:
        dates = sorted({d["date"] for d in vault.list_days()} | set(capture_days(vault)))
        days = [d for d in dates if has_inputs(vault, d)]
    else:
        days = [args.date]
    written = [write(vault, day, settings.timezone, labeler=labeler) for day in days]
    return {"category_llm": llm, "days": written} if args.all else {"category_llm": llm, **written[0]}


def week(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    from .review import weekly

    now = datetime.now(ZoneInfo(settings.timezone))
    day = {"last": weekly.due(now, settings.timezone), "today": now.date().isoformat()}.get(args.date, args.date)
    date.fromisoformat(day)
    out = weekly.write(vault, LifeStore(vault, zone=settings.timezone), day, settings.timezone, now=now)
    return {"written": out["written"], **out["week"]}


def review(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    from .review.summary import review_command

    return review_command(vault, settings, args.date, check=args.check)


def import_phone(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    options = {"transcribe": not args.no_transcribe, "audio_ends_at_creation": not args.audio_stamped_at_start}
    if args.inbox:
        from .phone.inbox import process

        return process(vault, say=say, **options) or {"imported": False}
    if not args.paths:
        args.fail("give the files to import, or --inbox")
    from .phone.importer import import_phone as run

    return run(vault, args.paths, say=say, **options)


def scan_moments(args: argparse.Namespace, vault: Vault) -> list[str]:
    from .splat.run import scans

    moments = [args.moment] if args.moment else scans(vault, args.date)
    if not moments:
        args.fail(f"no scans on {args.date}")
    return moments


def splat(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    from .splat.run import splat_moment

    return {
        m: splat_moment(vault, args.date, m, capture=args.capture, steps=args.steps, frames=args.frames,
                        train=not args.no_train, say=say)
        for m in scan_moments(args, vault)
    }  # fmt: skip


def mesh(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    from .splat.run import mesh_moment

    return {m: mesh_moment(vault, args.date, m, capture=args.capture, say=say) for m in scan_moments(args, vault)}


def jobs(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    from .jobs.stages import discover
    from .jobs.store import Jobs
    from .jobs.worker import run_next

    store = Jobs.of(vault.root)
    if args.action == "retry":
        result = {"queued_again": store.retry(stage=args.stage, job_id=args.id)}
    else:
        discover(vault, store)
        done = []
        if args.action == "run":
            idle = float("inf") if args.now else None
            worker = f"cli-{os.getpid()}"
            while (step := run_next(vault, store, worker, args.needs, idle_s=idle, say=say)) is not None:
                done.append(step)
        result = {"ran": done, **store.status()} if args.action == "run" else store.status()
    store.close()
    return result


def voices(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    from .voices import update

    gallery = update(vault)
    listed = [{k: v for k, v in voice.items() if k != "print"} for voice in gallery["voices"]]
    return {"same": gallery.get("same"), "voices": listed}


def compact(args: argparse.Namespace, vault: Vault, settings: Settings) -> Any:
    before = args.before or datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    return {d["date"]: vault.compact_day(d["date"]) for d in vault.list_days() if d["date"] < before}


def mcp(args: argparse.Namespace, settings: Settings) -> None:
    """Serves the vault itself, read-only (ingest/mcp_server.py): stdout carries the protocol."""
    from .mcp_server import client_config, serve

    if args.print_config:
        print(json.dumps(client_config(), indent=2))
    else:
        serve(settings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daycube", description="DAYCUBE local vault tools")
    parser.set_defaults(fail=parser.error)
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, run: Command, help: str | None = None) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=help)
        command.set_defaults(run=run)
        return command

    def day_or_all(command: argparse.ArgumentParser, help: str | None = None) -> None:
        which = command.add_mutually_exclusive_group(required=True)
        which.add_argument("--date", help=help)
        which.add_argument("--all", action="store_true")

    add("init", init, "Initialize vault and private bearer token")
    add("seed", seed, "Copy the synthetic fixture; never overwrite existing days").add_argument(
        "--capture", action="store_true", help="Also write a scripted morning of computer capture (2026-10-22)"
    )
    add("doctor", doctor, "Check local runtime and optional integrations")
    add("stats", stats, "Days, units and size of the vault database")
    add("validate", validate).add_argument("--date", required=True)
    day_or_all(add("index", index, "Index a day, or every day, into the vault database"))
    day_or_all(add("notes", notes, "Write the linked notes (vault/notes, Obsidian-compatible) of a day"))
    day_or_all(
        add("timeline", timeline, "Write a day's timeline (vault/<date>/timeline.jsonl): desk, away, places, talk"),
        "The review day: 04:00 that day to 04:00 the next",
    )
    add(
        "week", week, "Write the week (Monday to Sunday) a date is in: vault/weeks/<YYYY-Www>.json and its note"
    ).add_argument("--date", default="last", help="A date of the week; today, or last (the last finished week)")
    command = add("review", review, "Write a day's end-of-day review (vault/<date>/review.json) now, and print it")
    command.add_argument("--date", required=True, help="YYYY-MM-DD, today or yesterday (review days start at 04:00)")
    command.add_argument(
        "--check", action="store_true", help="Also check the words against the day's facts, and time it"
    )
    add("graph", graph, "The most mentioned entities and their links, as JSON")
    add("data", data, "The data center: every source stream and the database, with sizes")

    command = add(
        "import-phone", import_phone, "Import phone recordings: Sensor Logger, voice memos, Stray Scanner captures"
    )
    command.add_argument("paths", nargs="*", type=Path, help="Files or folders exported from the phone")
    command.add_argument("--inbox", action="store_true", help="Import what waits in vault/inbox/phone")
    command.add_argument("--no-transcribe", action="store_true", help="Keep voice memos without transcribing them")
    command.add_argument(
        "--audio-stamped-at-start",
        action="store_true",
        help="The recording app stamps audio files with their start, not their end (Voice Memos: the end)",
    )

    scan_help = {
        "--moment": "One scan moment (default: every scan of the day)",
        "--capture": "The Stray Scanner folder, if its depth is not in the vault",
    }
    command = add(
        "splat", splat, "Turn a day's LiDAR scans into 3D: a LiDAR preview at once, then a splat trained on the GPU"
    )
    command.add_argument("--date", required=True)
    command.add_argument("--moment", help=scan_help["--moment"])
    command.add_argument("--capture", type=Path, help=scan_help["--capture"])
    command.add_argument("--steps", type=int, default=30_000, help="Training steps (default 30000)")
    command.add_argument("--frames", type=int, default=1260, help="Video frames to train on (default 1260)")
    command.add_argument("--no-train", action="store_true", help="Only the training set and the LiDAR preview")
    command = add("mesh", mesh, "A day's LiDAR scans as meshes, from their depth (the mesh extra)")
    command.add_argument("--date", required=True)
    command.add_argument("--moment", help=scan_help["--moment"])
    command.add_argument("--capture", type=Path, help=scan_help["--capture"])

    command = add("jobs", jobs, "The vault's processing jobs (vault/jobs.sqlite): status, retry, run")
    command.add_argument("action", nargs="?", choices=["status", "retry", "run"], default="status")
    command.add_argument("--stage", help="retry: only this stage's failed jobs")
    command.add_argument("--id", type=int, help="retry: only this job")
    command.add_argument("--needs", choices=["cpu", "gpu"], default="cpu", help="run: which jobs (default cpu)")
    command.add_argument("--now", action="store_true", help="run: take the GPU now, at the PC or not")
    add("voices", voices, "Rebuild the voices across recordings and name them; how far apart they are")
    add("compact", compact, "Compress large logs of finished days; reads stay transparent").add_argument(
        "--before", help="Compact every day before this date (default: today)"
    )
    add("mcp", mcp, "Serve your memory to AI agents (Claude, Cursor, ...) over MCP on stdio; read-only").add_argument(
        "--print-config", action="store_true", help="Print the settings to paste into an MCP client"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings()
    if args.run is mcp:
        return mcp(args, settings)
    vault = Vault(settings.vault_root)
    vault.initialize()
    print(json.dumps(args.run(args, vault, settings), indent=2))


if __name__ == "__main__":
    main()

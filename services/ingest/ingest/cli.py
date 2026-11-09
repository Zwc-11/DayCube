import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .index.embed import default_embedder
from .store import LifeStore
from .vault import Vault


def main() -> None:
    parser = argparse.ArgumentParser(prog="daycube", description="DAYCUBE local vault tools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize vault and private bearer token")
    seed = commands.add_parser("seed", help="Copy the synthetic fixture; never overwrite existing days")
    seed.add_argument(
        "--capture", action="store_true", help="Also write a scripted morning of computer capture (2026-10-22)"
    )
    commands.add_parser("doctor", help="Check local runtime and optional integrations")
    commands.add_parser("stats", help="Days, units and size of the vault database")
    validate = commands.add_parser("validate")
    validate.add_argument("--date", required=True)
    index = commands.add_parser("index", help="Index a day, or every day, into the vault database")
    which = index.add_mutually_exclusive_group(required=True)
    which.add_argument("--date")
    which.add_argument("--all", action="store_true")
    notes = commands.add_parser("notes", help="Write the linked notes (vault/notes, Obsidian-compatible) of a day")
    which_notes = notes.add_mutually_exclusive_group(required=True)
    which_notes.add_argument("--date")
    which_notes.add_argument("--all", action="store_true")
    timeline = commands.add_parser(
        "timeline", help="Write a day's timeline (vault/<date>/timeline.jsonl): desk, away, places, talk"
    )
    which_timeline = timeline.add_mutually_exclusive_group(required=True)
    which_timeline.add_argument("--date", help="The review day: 04:00 that day to 04:00 the next")
    which_timeline.add_argument("--all", action="store_true")
    week = commands.add_parser(
        "week", help="Write the week (Monday to Sunday) a date is in: vault/weeks/<YYYY-Www>.json and its note"
    )
    week.add_argument("--date", default="last", help="A date of the week; today, or last (the last finished week)")
    review = commands.add_parser(
        "review", help="Write a day's end-of-day review (vault/<date>/review.json) now, and print it"
    )
    review.add_argument("--date", required=True, help="YYYY-MM-DD, today or yesterday (review days start at 04:00)")
    review.add_argument("--check", action="store_true", help="Also check the words against the day's facts, and time it")
    commands.add_parser("graph", help="The most mentioned entities and their links, as JSON")
    commands.add_parser("data", help="The data center: every source stream and the database, with sizes")
    phone = commands.add_parser(
        "import-phone", help="Import phone recordings: Sensor Logger, voice memos, Stray Scanner captures"
    )
    phone.add_argument("paths", nargs="*", type=Path, help="Files or folders exported from the phone")
    phone.add_argument("--inbox", action="store_true", help="Import what waits in vault/inbox/phone")
    phone.add_argument("--no-transcribe", action="store_true", help="Keep voice memos without transcribing them")
    phone.add_argument(
        "--audio-stamped-at-start",
        action="store_true",
        help="The recording app stamps audio files with their start, not their end (Voice Memos: the end)",
    )
    splat = commands.add_parser(
        "splat", help="Turn a day's LiDAR scans into 3D: a LiDAR preview at once, then a splat trained on the GPU"
    )
    splat.add_argument("--date", required=True)
    splat.add_argument("--moment", help="One scan moment (default: every scan of the day)")
    splat.add_argument("--capture", type=Path, help="The Stray Scanner folder, if its depth is not in the vault")
    splat.add_argument("--steps", type=int, default=30_000, help="Training steps (default 30000)")
    splat.add_argument("--frames", type=int, default=1260, help="Video frames to train on (default 1260)")
    splat.add_argument("--no-train", action="store_true", help="Only the training set and the LiDAR preview")
    mesh = commands.add_parser("mesh", help="A day's LiDAR scans as meshes, from their depth (the mesh extra)")
    mesh.add_argument("--date", required=True)
    mesh.add_argument("--moment", help="One scan moment (default: every scan of the day)")
    mesh.add_argument("--capture", type=Path, help="The Stray Scanner folder, if its depth is not in the vault")
    jobs = commands.add_parser("jobs", help="The vault's processing jobs (vault/jobs.sqlite): status, retry, run")
    jobs.add_argument("action", nargs="?", choices=["status", "retry", "run"], default="status")
    jobs.add_argument("--stage", help="retry: only this stage's failed jobs")
    jobs.add_argument("--id", type=int, help="retry: only this job")
    jobs.add_argument("--needs", choices=["cpu", "gpu"], default="cpu", help="run: which jobs (default cpu)")
    jobs.add_argument("--now", action="store_true", help="run: take the GPU now, at the PC or not")
    commands.add_parser("voices", help="Rebuild the voices across recordings and name them; how far apart they are")
    compact = commands.add_parser("compact", help="Compress large logs of finished days; reads stay transparent")
    compact.add_argument("--before", help="Compact every day before this date (default: today)")
    args = parser.parse_args()
    settings = Settings()
    vault = Vault(settings.vault_root)
    vault.initialize()
    if args.command == "init":
        result = {"vault": str(vault.root), "token_file": str(vault.root / ".token")}
    elif args.command == "seed":
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
    elif args.command == "validate":
        result = vault.validate(args.date)
    elif args.command == "timeline":
        from .capture import capture_days
        from .review import has_inputs, write
        from .review.labels import llm_labeler

        labeler, llm = llm_labeler(settings.category_llm)
        if args.all:
            dates = sorted({d["date"] for d in vault.list_days()} | set(capture_days(vault)))
            days = [d for d in dates if has_inputs(vault, d)]
        else:
            days = [args.date]
        result = [write(vault, day, settings.timezone, labeler=labeler) for day in days]
        result = {"category_llm": llm, "days": result} if args.all else {"category_llm": llm, **result[0]}
    elif args.command == "week":
        from datetime import date as _date
        from datetime import datetime as _datetime
        from zoneinfo import ZoneInfo as _Zone

        from .review import weekly

        now = _datetime.now(_Zone(settings.timezone))
        day = {"last": weekly.due(now, settings.timezone), "today": now.date().isoformat()}.get(args.date, args.date)
        _date.fromisoformat(day)
        out = weekly.write(vault, LifeStore(vault, zone=settings.timezone), day, settings.timezone, now=now)
        result = {"written": out["written"], **out["week"]}
    elif args.command == "review":
        from .review.summary import review_command

        result = review_command(vault, settings, args.date, check=args.check)
    elif args.command in {"index", "stats", "notes", "graph", "data"}:
        store = LifeStore(
            vault, default_embedder(settings.vector_search, settings.embedding_model), zone=settings.timezone
        )
        if args.command == "stats":
            result = store.stats()
        elif args.command == "graph":
            result = store.graph(limit=100)
        elif args.command == "data":
            from .datacenter import report

            result = report(store)
        elif args.command == "notes":
            from .notes import Notes

            writer = Notes(store)
            days = store.days() if args.all else [args.date]
            paths = sorted({p.relative_to(writer.root).as_posix() for day in days for p in writer.write_day(day)})
            result = {"folder": str(writer.root), "notes": paths}
        else:
            result = [store.update(day) for day in store.days()] if args.all else store.update(args.date)
    elif args.command == "import-phone":
        options = {"transcribe": not args.no_transcribe, "audio_ends_at_creation": not args.audio_stamped_at_start}
        if args.inbox:
            from .phone.inbox import process

            result = process(vault, say=lambda m: print(m, file=sys.stderr), **options) or {"imported": False}
        elif not args.paths:
            parser.error("give the files to import, or --inbox")
        else:
            from .phone.importer import import_phone

            result = import_phone(vault, args.paths, say=lambda m: print(m, file=sys.stderr), **options)
    elif args.command == "splat":
        from .splat.run import scans, splat_moment

        moments = [args.moment] if args.moment else scans(vault, args.date)
        if not moments:
            parser.error(f"no scans on {args.date}")
        result = {
            m: splat_moment(vault, args.date, m, capture=args.capture, steps=args.steps, frames=args.frames,
                            train=not args.no_train, say=lambda line: print(line, file=sys.stderr, flush=True))
            for m in moments
        }  # fmt: skip
    elif args.command == "mesh":
        from .splat.run import mesh_moment, scans

        moments = [args.moment] if args.moment else scans(vault, args.date)
        if not moments:
            parser.error(f"no scans on {args.date}")
        result = {m: mesh_moment(vault, args.date, m, capture=args.capture,
                                 say=lambda line: print(line, file=sys.stderr, flush=True)) for m in moments}  # fmt: skip
    elif args.command == "jobs":
        from .jobs.stages import discover
        from .jobs.store import Jobs
        from .jobs.worker import run_next

        store = Jobs.of(vault.root)
        if args.action == "retry":
            result = {"queued_again": store.retry(stage=args.stage, job_id=args.id)}
        elif args.action == "run":
            discover(vault, store)
            done = []
            idle = float("inf") if args.now else None
            while (step := run_next(vault, store, f"cli-{os.getpid()}", args.needs, idle_s=idle,
                                    say=lambda line: print(line, file=sys.stderr, flush=True))) is not None:  # fmt: skip
                done.append(step)
            result = {"ran": done, **store.status()}
        else:
            discover(vault, store)
            result = store.status()
        store.close()
    elif args.command == "voices":
        from .voices import update

        gallery = update(vault)
        result = {"same": gallery.get("same"), "voices": [{k: v for k, v in voice.items() if k != "print"}
                                                          for voice in gallery["voices"]]}  # fmt: skip
    elif args.command == "compact":
        before = args.before or datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
        result = {d["date"]: vault.compact_day(d["date"]) for d in vault.list_days() if d["date"] < before}
    else:
        import importlib.util
        import shutil
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE VIRTUAL TABLE test USING fts5(text)")
        result = {
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
            "fts5": True,
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "vault": str(vault.root),
            "days": len(vault.list_days()),
            "optional_libraries": {
                name: importlib.util.find_spec(name) is not None for name in ("anthropic", "modal", "fastembed")
            },
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

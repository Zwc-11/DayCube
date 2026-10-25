import argparse
import json
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
    splat.add_argument("--frames", type=int, default=420, help="Video frames to train on (default 420)")
    splat.add_argument("--no-train", action="store_true", help="Only the training set and the LiDAR preview")
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

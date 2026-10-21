import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Settings
from .index.build import build_index
from .vault import Vault


def main() -> None:
    parser = argparse.ArgumentParser(prog="daycube", description="DAYCUBE local vault tools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Initialize vault and private bearer token")
    commands.add_parser("seed", help="Copy the synthetic fixture; never overwrite existing days")
    commands.add_parser("doctor", help="Check local runtime and optional integrations")
    for name in ("validate", "index"):
        child = commands.add_parser(name)
        child.add_argument("--date", required=True)
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
    elif args.command == "validate":
        result = vault.validate(args.date)
    elif args.command == "index":
        result = build_index(vault, args.date)
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
                name: importlib.util.find_spec(name) is not None
                for name in ("anthropic", "modal", "fastembed", "sqlite_vec")
            },
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

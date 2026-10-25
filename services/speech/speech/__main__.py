"""``python -m speech``: run the speech worker, transcribe one moment, or check the models on clips
with known words and speakers."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def vault_root(given: str | None) -> Path:
    return Path(given or os.environ.get("DAYCUBE_VAULT_ROOT") or PROJECT_ROOT / "vault")


def main() -> None:
    parser = argparse.ArgumentParser(prog="speech", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Lease speech jobs from the capture receiver and run them")
    serve.add_argument("--vault")
    serve.add_argument("--receiver", default="http://127.0.0.1:7700")
    serve.add_argument("--once", action="store_true", help="Stop when no job is left")
    one = commands.add_parser("moment", help="Transcribe one moment now")
    one.add_argument("--vault")
    one.add_argument("--date", required=True)
    one.add_argument("--moment", required=True)
    canary = commands.add_parser("canary", help="Check the models on clips with known words and speakers")
    canary.add_argument("--device")
    args = parser.parse_args()

    if args.command == "serve":
        root = vault_root(args.vault)
        (root / "logs").mkdir(parents=True, exist_ok=True)
        handlers: list[logging.Handler] = [
            RotatingFileHandler(root / "logs" / "speech.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        ]
        if sys.stderr is not None and sys.stderr.isatty():
            handlers.append(logging.StreamHandler())
        logging.basicConfig(
            level=logging.INFO, handlers=handlers, format="%(asctime)s %(name)s %(levelname)s %(message)s"
        )
        from .worker import serve as run

        run(root, args.receiver, once=args.once)
    elif args.command == "moment":
        from .pipeline import Models, run_moment

        print(json.dumps(run_moment(vault_root(args.vault), args.date, args.moment, Models(),
                                    say=lambda m: print(m, file=sys.stderr)), ensure_ascii=False))  # fmt: skip
    else:
        from .canary import run as check

        result = check(device=args.device, say=lambda m: print(m, file=sys.stderr))
        print(json.dumps(result, ensure_ascii=False, indent=1))
        sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()

"""A folder to drop phone exports into: ``vault/inbox/phone``.

Whatever lands there (Sensor Logger zips, voice memos, Stray Scanner folders or their files,
transcripts) is imported together once nothing in it has changed for ``SETTLE_S`` (a copy or a
sync still in progress is left alone), then moved to ``imported/<when>/``. A failed import is
logged in ``errors.jsonl`` and its files are not tried again until one of them changes, so a bad
file cannot make the receiver retry it every minute. The capture receiver looks every minute
(``capture.inbox``); ``daycube import-phone --inbox`` does it once.
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..vault import Vault

SETTLE_S = 30.0
SKIP = frozenset({"imported", "errors.jsonl", "imported.jsonl", "README.txt"})


def folder(vault: Vault) -> Path:
    path = vault.root / "inbox" / "phone"
    path.mkdir(parents=True, exist_ok=True)
    readme = path / "README.txt"
    if not readme.exists():
        readme.write_text(
            "Drop phone exports here: Sensor Logger zips, voice memos, Stray Scanner captures.\n"
            "DAYCUBE imports them into the day they were recorded and moves them to imported/.\n",
            encoding="utf-8",
        )
    return path


def _stamp(paths: list[Path]) -> list[list]:
    out = []
    for path in sorted(paths):
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        out += [[str(p.relative_to(path.parent)), p.stat().st_size, p.stat().st_mtime_ns] for p in files]
    return out


def process(vault: Vault, *, now: float | None = None, say: Any = print, **options: Any) -> dict | None:
    """Import what waits in the inbox (see the module); None when there is nothing to do."""
    from .importer import import_phone

    inbox = folder(vault)
    waiting = [p for p in inbox.iterdir() if p.name not in SKIP and not p.name.startswith(".")]
    if not waiting:
        return None
    now = time.time() if now is None else now
    stamp = _stamp(waiting)
    if any(now - mtime / 1e9 < SETTLE_S for _, _, mtime in stamp):
        return None  # still being copied
    errors = inbox / "errors.jsonl"
    if errors.exists():
        failed = [json.loads(line).get("stamp") for line in errors.read_text("utf-8").splitlines() if line]
        waiting = [w for w in waiting if _stamp([w]) not in failed]  # failed before, unchanged since
        if not waiting or stamp in failed:
            return None
        stamp = _stamp(waiting)
    when = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    try:
        result = import_phone(vault, waiting, say=say, **options)
    except Exception as error:  # noqa: BLE001 - recorded, and not retried until the files change
        with errors.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"t": when, "error": f"{type(error).__name__}: {error}", "stamp": stamp}) + "\n")
        say(f"phone inbox: could not import {len(waiting)} item(s): {error}")
        return {"imported": False, "error": str(error)}
    # What was left out (a memo with FFmpeg missing) stays, noted, until it changes.
    left = {Path(p).resolve() for item in result["skipped"] for p in item["paths"]}
    stays = [w for w in waiting if any(p == w.resolve() or w.resolve() in p.parents for p in left)]
    if stays:
        reasons = "; ".join(item["reason"] for item in result["skipped"])
        with errors.open("a", encoding="utf-8") as out:
            for path in stays:
                out.write(json.dumps({"t": when, "error": f"left out: {reasons}", "stamp": _stamp([path])}) + "\n")
    moved = [w for w in waiting if w not in stays]
    done = inbox / "imported" / when
    done.mkdir(parents=True, exist_ok=True)
    for path in moved:
        shutil.move(str(path), done / path.name)
    with (inbox / "imported.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps({"t": when, "items": [p.name for p in moved], "days": result["days"],
                              "left": [p.name for p in stays]}) + "\n")  # fmt: skip
    return {"imported": True, "days": result["days"], "left": [p.name for p in stays], "moved_to": str(done)}

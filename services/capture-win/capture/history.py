"""Import Opera's browsing history (any Chromium browser's) into ``vault/capture/<date>/history.jsonl``.

The extension records from the moment it is installed; the browser's own history reaches back
about 90 days and keeps recording when the extension is off. Each import mirrors the browser's
current history for the days it covers: days are rewritten, and a day whose visits are all gone
(deleted in the browser, or newly excluded) loses its file.

Left out, as the extension would leave them out: domains excluded in the extension (mirrored to
``vault/capture/exclusions.json``) or in ``config/exclude.yaml``, URL and title patterns from that
file, and visits made while recording was paused (``vault/capture/pauses.jsonl``).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml
from ingest.vault import Vault, read_lines

EPOCH = datetime(1601, 1, 1, tzinfo=UTC)  # Chromium stores microseconds since this instant
CORE = {
    0: "link", 1: "typed", 2: "auto_bookmark", 3: "auto_subframe", 4: "manual_subframe", 5: "generated",
    6: "auto_toplevel", 7: "form_submit", 8: "reload", 9: "keyword", 10: "keyword_generated",
}  # fmt: skip
QUALIFIERS = {
    0x01000000: "forward_back", 0x02000000: "from_address_bar", 0x04000000: "home_page", 0x08000000: "from_api",
    0x10000000: "chain_start", 0x20000000: "chain_end", 0x40000000: "client_redirect", 0x80000000: "server_redirect",
}  # fmt: skip
EXCLUDE_FILE = Path(__file__).resolve().parents[1] / "config" / "exclude.yaml"


@dataclass
class Exclusions:
    domains: list[str] = field(default_factory=list)
    url_patterns: list[str] = field(default_factory=list)
    title_patterns: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path = EXCLUDE_FILE, extra_domains: Iterable[str] = ()) -> Exclusions:
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None) or {}
        domains = [d.strip().lower().lstrip(".") for d in [*(data.get("domains") or []), *extra_domains] if d.strip()]
        return cls(domains, list(data.get("url_patterns") or []), list(data.get("title_patterns") or []))

    @classmethod
    def for_vault(cls, vault: Vault, extra_domains: Iterable[str] = (), path: Path = EXCLUDE_FILE) -> Exclusions:
        """exclude.yaml plus the domains the extension excludes."""
        mirrored = vault.root / "capture" / "exclusions.json"
        domains = json.loads(mirrored.read_text(encoding="utf-8"))["domains"] if mirrored.is_file() else []
        return cls.load(path, [*domains, *extra_domains])

    def excluded(self, url: str, title: str = "") -> bool:
        host = (urlsplit(url).hostname or "").lower()
        if any(host == d or host.endswith("." + d) for d in self.domains):
            return True
        if any(fnmatch.fnmatchcase(url, pattern) for pattern in self.url_patterns):
            return True
        return any(re.search(pattern, title) for pattern in self.title_patterns)


def paused_spans(vault: Vault) -> list[tuple[datetime, datetime]]:
    """When recording was paused, from the receiver's pause log; an open pause runs until now."""
    spans: list[tuple[datetime, datetime]] = []
    start: datetime | None = None
    for entry in read_lines(vault.root / "capture" / "pauses.jsonl"):
        t = datetime.fromisoformat(entry["t"])
        if entry["paused"] and start is None:
            start = t
        elif not entry["paused"] and start is not None:
            spans.append((start, t))
            start = None
    if start is not None:
        spans.append((start, datetime.now(UTC)))
    return spans


def opera_histories() -> list[Path]:
    """Opera's History files on this computer, one per installation. Newer versions keep the
    profile in ``Default``; a ``History`` left beside it by an older version is ignored."""
    root = Path(os.environ.get("APPDATA", "")) / "Opera Software"
    found = []
    for name in ("Opera Stable", "Opera GX Stable"):
        for candidate in (root / name / "Default" / "History", root / name / "History"):
            if candidate.is_file():
                found.append(candidate)
                break
    return found


def read_visits(history: Path, zone: ZoneInfo, since: datetime | None = None) -> tuple[list[dict], date | None]:
    """Top-level http(s) visits, oldest first, and the local day of the oldest visit the file
    keeps (of any kind). The browser keeps the file open, so it is copied first."""
    after = 0 if since is None else int((since - EPOCH) / timedelta(microseconds=1))
    # Ids stay the same across imports and differ between History files.
    source = hashlib.blake2b(str(history.resolve()).lower().encode(), digest_size=3).hexdigest()
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "History"
        shutil.copyfile(history, copy)
        for suffix in ("-journal", "-wal"):
            if history.with_name(history.name + suffix).is_file():
                shutil.copyfile(history.with_name(history.name + suffix), copy.with_name(copy.name + suffix))
        with closing(sqlite3.connect(copy)) as db:
            oldest = db.execute("SELECT MIN(visit_time) FROM visits WHERE visit_time > 0").fetchone()[0]
            rows = db.execute(
                "SELECT v.id, v.visit_time, v.from_visit, v.transition, v.visit_duration, u.url, u.title "
                "FROM visits v JOIN urls u ON u.id = v.url WHERE v.visit_time > ? ORDER BY v.visit_time, v.id",
                (after,),
            ).fetchall()
    visits = []
    for visit_id, micros, origin, transition, duration, url, title in rows:
        transition &= 0xFFFFFFFF  # stored as a signed 32-bit integer
        core = CORE.get(transition & 0xFF, "other")
        if core in ("auto_subframe", "manual_subframe") or not url.startswith(("http://", "https://")):
            continue
        visits.append({
            "id": f"visit-{source}-{visit_id}",
            "t": (EPOCH + timedelta(microseconds=micros)).astimezone(zone).isoformat(),
            "kind": "visit",
            "url": url,
            "title": title or "",
            "transition": core,
            "qualifiers": [name for bit, name in QUALIFIERS.items() if transition & bit],
            "duration_s": round(duration / 1e6, 1) if duration else None,
            "from": f"visit-{source}-{origin}" if origin else None,
            "source": history.parent.name if history.parent.name != "Default" else history.parent.parent.name,
        })  # fmt: skip
    first = (EPOCH + timedelta(microseconds=oldest)).astimezone(zone).date() if oldest else None
    return visits, first


def import_history(
    vault: Vault,
    zone: ZoneInfo,
    histories: list[Path],
    *,
    since: date | None = None,
    exclusions: Exclusions | None = None,
) -> dict[str, int]:
    """Write one ``history.jsonl`` per day; returns visits written per day (0: file removed)."""
    exclusions = exclusions or Exclusions()
    start = None if since is None else datetime.combine(since, time(), zone)
    paused = paused_spans(vault)
    days: dict[str, list[dict]] = {}
    oldest: date | None = None
    for history in histories:
        visits, first = read_visits(history, zone, start)
        if first is not None:
            oldest = first if oldest is None else min(oldest, first)
        for visit in visits:
            t = datetime.fromisoformat(visit["t"])
            if exclusions.excluded(visit["url"], visit["title"]) or any(a <= t < b for a, b in paused):
                continue
            days.setdefault(visit["t"][:10], []).append(visit)
    # Days the browser still covers: from the oldest visit it keeps (or ``since``) to today.
    covered = max(filter(None, (oldest, since)), default=None)
    written: dict[str, int] = {}
    with vault.lock:
        for day, visits in sorted(days.items()):
            visits.sort(key=lambda v: (v["t"], v["id"]))
            vault.write_lines(vault.root / "capture" / day / "history.jsonl", visits)
            written[day] = len(visits)
        if covered is not None:
            for path in sorted((vault.root / "capture").glob("*/history.jsonl")):
                day = path.parent.name
                if day not in days and day >= covered.isoformat():
                    path.unlink()
                    written[day] = 0
    return dict(sorted(written.items()))

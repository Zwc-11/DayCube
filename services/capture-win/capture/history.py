"""Import Opera's browsing history (any Chromium browser's) into ``vault/capture/<date>/history.jsonl``.

The extension records from the moment it is installed; the browser's own history reaches back
about 90 days and keeps recording when the extension is off. Each import rewrites the files of
the days it covers, so running it again mirrors the browser's current history for those days
(including visits deleted there) instead of adding duplicates.
"""

from __future__ import annotations

import fnmatch
import os
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
from ingest.vault import Vault

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

    @classmethod
    def load(cls, path: Path = EXCLUDE_FILE, extra_domains: Iterable[str] = ()) -> Exclusions:
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None) or {}
        domains = [d.strip().lower().lstrip(".") for d in [*(data.get("domains") or []), *extra_domains] if d.strip()]
        return cls(domains, list(data.get("url_patterns") or []))

    def excluded(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        if any(host == d or host.endswith("." + d) for d in self.domains):
            return True
        return any(fnmatch.fnmatchcase(url, pattern) for pattern in self.url_patterns)


def opera_histories() -> list[Path]:
    """Opera's History files on this computer: newer versions keep the profile in ``Default``."""
    root = Path(os.environ.get("APPDATA", "")) / "Opera Software"
    names = ("Opera Stable", "Opera GX Stable")
    return [p for name in names for p in (root / name / "Default" / "History", root / name / "History") if p.is_file()]


def read_visits(history: Path, zone: ZoneInfo, since: datetime | None = None) -> list[dict]:
    """Top-level http(s) visits, oldest first. The browser keeps the file open, so it is copied first."""
    after = 0 if since is None else int((since - EPOCH) / timedelta(microseconds=1))
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "History"
        shutil.copyfile(history, copy)
        for suffix in ("-journal", "-wal"):
            if history.with_name(history.name + suffix).is_file():
                shutil.copyfile(history.with_name(history.name + suffix), copy.with_name(copy.name + suffix))
        with closing(sqlite3.connect(copy)) as db:
            rows = db.execute(
                "SELECT v.id, v.visit_time, v.from_visit, v.transition, v.visit_duration, u.url, u.title "
                "FROM visits v JOIN urls u ON u.id = v.url WHERE v.visit_time > ? ORDER BY v.visit_time, v.id",
                (after,),
            ).fetchall()
    visits = []
    for visit_id, micros, source, transition, duration, url, title in rows:
        transition &= 0xFFFFFFFF  # stored as a signed 32-bit integer
        core = CORE.get(transition & 0xFF, "other")
        if core in ("auto_subframe", "manual_subframe") or not url.startswith(("http://", "https://")):
            continue
        visits.append({
            "id": f"visit-{visit_id}",
            "t": (EPOCH + timedelta(microseconds=micros)).astimezone(zone).isoformat(),
            "kind": "visit",
            "url": url,
            "title": title or "",
            "transition": core,
            "qualifiers": [name for bit, name in QUALIFIERS.items() if transition & bit],
            "duration_s": round(duration / 1e6, 1) if duration else None,
            "from": f"visit-{source}" if source else None,
        })  # fmt: skip
    return visits


def import_history(
    vault: Vault, zone: ZoneInfo, history: Path, *, since: date | None = None, exclusions: Exclusions | None = None
) -> dict[str, int]:
    """Write one ``history.jsonl`` per day with visits; returns visits written per day."""
    exclusions = exclusions or Exclusions()
    start = None if since is None else datetime.combine(since, time(), zone)
    days: dict[str, list[dict]] = {}
    for visit in read_visits(history, zone, start):
        if not exclusions.excluded(visit["url"]):
            days.setdefault(visit["t"][:10], []).append(visit)
    with vault.lock:
        for day, visits in days.items():
            vault.write_lines(vault.root / "capture" / day / "history.jsonl", visits)
    return {day: len(visits) for day, visits in sorted(days.items())}

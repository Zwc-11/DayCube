"""Keeps the day's capture searchable and its linked notes current while the receiver runs.

Every ``every_s`` (10 minutes) it looks at today's capture logs (and yesterday's, until that day's
last changes are in), and when they changed it brings the day into the vault database and writes
its notes (``vault/notes``: the daily note, the pages that held attention, the courses). So
"what did I read about X" and the Obsidian folder follow the day without anyone running a
command. It uses its own ``Vault`` object, so it never holds the lock the receiver appends under,
and keyword search only: embedding the day's text is left to ``daycube index`` or the next search.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("capture.librarian")

EVERY_S = 600.0
FIRST_S = 60.0  # after start, so a sign-in is not slowed by it


class Librarian(threading.Thread):
    def __init__(self, vault_root: Path, zone: str, every_s: float = EVERY_S) -> None:
        super().__init__(name="librarian", daemon=True)
        self.vault_root, self.zone, self.every_s = vault_root, zone, every_s
        self.stopping = threading.Event()
        self.seen: dict[str, dict] = {}  # day -> capture stamp its notes were last written from

    def run(self) -> None:
        if self.stopping.wait(FIRST_S):
            return
        while True:
            try:
                self.step()
            except Exception:  # noqa: BLE001 - logged; the next round tries again
                log.exception("notes for the day could not be written")
            if self.stopping.wait(self.every_s):
                return

    def step(self, now: datetime | None = None) -> list[str]:
        """Write the notes of today, and yesterday, when their capture changed; returns the days written."""
        from ingest.capture import capture_stamp
        from ingest.notes import Notes
        from ingest.store import LifeStore
        from ingest.vault import Vault

        now = now or datetime.now(ZoneInfo(self.zone))
        today = now.date()
        vault = Vault(self.vault_root)
        store = LifeStore(vault, zone=self.zone)
        written = []
        for day in ((today - timedelta(days=1)).isoformat(), today.isoformat()):
            stamp = capture_stamp(vault, day)
            if not any(key.startswith(f"capture/{day}/") for key in stamp) or self.seen.get(day) == stamp:
                continue
            paths = Notes(store).write_day(day)
            self.seen[day] = stamp
            written.append(day)
            log.info("notes for %s: %d written", day, len(paths))
        for day in [d for d in self.seen if d < (today - timedelta(days=1)).isoformat()]:
            del self.seen[day]
        return written

    def stop(self) -> None:
        self.stopping.set()

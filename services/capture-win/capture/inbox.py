"""Imports phone exports dropped into ``vault/inbox/phone`` while the receiver runs
(``ingest.phone.inbox``): every minute it looks, and what has stopped changing is imported into
the day it was recorded. Its own ``Vault`` object, like the librarian's."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger("capture.inbox")

EVERY_S = 60.0
FIRST_S = 30.0


class PhoneInbox(threading.Thread):
    def __init__(self, vault_root: Path, every_s: float = EVERY_S) -> None:
        super().__init__(name="phone inbox", daemon=True)
        self.vault_root, self.every_s = vault_root, every_s
        self.stopping = threading.Event()

    def run(self) -> None:
        if self.stopping.wait(FIRST_S):
            return
        while True:
            try:
                self.step()
            except Exception:  # noqa: BLE001 - logged; the next round tries again
                log.exception("phone inbox failed")
            if self.stopping.wait(self.every_s):
                return

    def step(self) -> dict | None:
        from ingest.phone.inbox import process
        from ingest.vault import Vault

        result = process(Vault(self.vault_root), say=log.info)
        if result and result.get("imported"):
            log.info("phone inbox: imported %s", ", ".join(result["days"]))
        return result

    def stop(self) -> None:
        self.stopping.set()

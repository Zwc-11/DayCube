"""Opera history import, the sign-in entry and the eye-tracker supervisor."""

import json
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from capture import autostart
from capture.history import EPOCH, Exclusions, import_history
from capture.supervise import GazeSupervisor
from ingest.vault import Vault

TORONTO = ZoneInfo("America/Toronto")


def chromium_time(iso: str) -> int:
    return int((datetime.fromisoformat(iso) - EPOCH).total_seconds() * 1_000_000)


def history_file(path: Path, visits: list[tuple]) -> Path:
    """A History file with Chromium's columns: (id, url, title, time, transition, duration s, from)."""
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(
            "CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT);"
            "CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER, from_visit INTEGER,"
            " transition INTEGER, visit_duration INTEGER);"
        )
        for visit_id, url, title, iso, transition, seconds, source in visits:
            db.execute("INSERT INTO urls (id, url, title) VALUES (?, ?, ?)", (visit_id, url, title))
            signed = transition - (1 << 32) if transition >= 1 << 31 else transition  # Chromium's signed int
            db.execute(
                "INSERT INTO visits VALUES (?, ?, ?, ?, ?, ?)",
                (visit_id, visit_id, chromium_time(iso), source, signed, int(seconds * 1e6)),
            )
    return path


def test_history_import_keeps_top_level_visits_by_local_day(vault: Vault, tmp_path: Path) -> None:
    history = history_file(tmp_path / "History", [
        (1, "https://arxiv.org/abs/2406.01234", "A paper", "2026-10-21T03:30:00+00:00", 0x30000001, 42.5, 0),
        (2, "https://arxiv.org/pdf/2406.01234", "PDF", "2026-10-21T03:31:00+00:00", 0x00000000, 0, 1),
        (3, "https://ads.example.com/frame", "", "2026-10-21T03:31:01+00:00", 0x00000003, 0, 0),
        (4, "https://www.mybank.com/login", "Bank", "2026-10-21T15:00:00+00:00", 0x02000001, 0, 0),
        (5, "chrome://settings/", "Settings", "2026-10-21T15:01:00+00:00", 0x00000001, 0, 0),
        (6, "https://news.ycombinator.com/", "HN", "2026-10-21T16:00:00+00:00", 0x80000008, 3, 0),
    ])  # fmt: skip
    exclusions = Exclusions(domains=["mybank.com"])
    assert import_history(vault, TORONTO, history, exclusions=exclusions) == {"2026-10-20": 2, "2026-10-21": 1}
    evening = [json.loads(line) for line in (vault.root / "capture/2026-10-20/history.jsonl").open()]
    assert [(v["id"], v["transition"], v["qualifiers"], v["duration_s"], v["from"]) for v in evening] == [
        ("visit-1", "typed", ["chain_start", "chain_end"], 42.5, None),
        ("visit-2", "link", [], None, "visit-1"),
    ]
    assert evening[0]["t"] == "2026-10-20T23:30:00-04:00"  # 03:30 UTC is the previous evening in Toronto
    later = json.loads((vault.root / "capture/2026-10-21/history.jsonl").read_text())
    assert (later["transition"], later["qualifiers"]) == ("reload", ["server_redirect"])
    # Importing again rewrites those days rather than appending duplicates.
    assert import_history(vault, TORONTO, history, since=date(2026, 10, 21), exclusions=exclusions) == {"2026-10-21": 1}
    assert len((vault.root / "capture/2026-10-20/history.jsonl").read_text().splitlines()) == 2


class FakeRegistry(SimpleNamespace):
    """The parts of ``winreg`` the sign-in entry uses."""

    HKEY_CURRENT_USER, KEY_SET_VALUE, KEY_QUERY_VALUE, REG_SZ = 1, 2, 3, 4

    def __init__(self) -> None:
        super().__init__(values={})

    def CreateKeyEx(self, root: int, path: str, reserved: int, access: int) -> "FakeRegistry":  # noqa: N802
        return self

    OpenKey = CreateKeyEx

    def __enter__(self) -> "FakeRegistry":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def SetValueEx(self, key: object, name: str, reserved: int, kind: int, value: str) -> None:  # noqa: N802
        self.values[name] = value

    def QueryValueEx(self, key: object, name: str) -> tuple[str, int]:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def DeleteValue(self, key: object, name: str) -> None:  # noqa: N802
        if self.values.pop(name, None) is None:
            raise FileNotFoundError(name)


def test_the_sign_in_entry_runs_the_windowless_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = FakeRegistry()
    monkeypatch.setattr(autostart, "_registry", lambda: registry)
    scripts = tmp_path / "Program Files" / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "pythonw.exe").touch()
    command = autostart.launch_command(gaze=True, interpreter=scripts / "python.exe")
    assert command == [str(scripts / "pythonw.exe"), "-m", "capture", "run", "--receive", "--gaze"]
    line = autostart.install(command)
    assert line.startswith(f'"{scripts / "pythonw.exe"}" -m capture run')  # quoted: the path has a space
    assert autostart.current() == line
    assert autostart.uninstall() is True and autostart.current() is None and autostart.uninstall() is False


def test_the_supervisor_waits_for_calibration_then_runs_and_stops_the_tracker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "fake" / "gaze"
    fake.mkdir(parents=True)
    (fake / "__init__.py").touch()
    out = tmp_path / "argv.json"
    (fake / "__main__.py").write_text(
        f"import json, pathlib, sys, time\npathlib.Path({str(out)!r}).write_text(json.dumps(sys.argv[1:]))\ntime.sleep(60)\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fake"))
    vault_root = tmp_path / "vault"
    supervisor = GazeSupervisor(Path(sys.executable), vault_root, "http://127.0.0.1:7700")
    monkeypatch.setattr(
        supervisor.stopping, "wait", lambda timeout=None: (time.sleep(0.05), supervisor.stopping.is_set())[1]
    )
    supervisor.start()
    time.sleep(0.3)
    assert supervisor.process is None and not out.exists()  # no calibration yet
    (vault_root / "calibration").mkdir(parents=True)
    (vault_root / "calibration" / "gaze.json").write_text("{}")
    deadline = time.monotonic() + 15
    while not out.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert json.loads(out.read_text())[:3] == ["track", "--vault", str(vault_root)]
    supervisor.stop()
    supervisor.join(10)
    assert supervisor.process.poll() is not None and not supervisor.is_alive()


def test_old_capture_command_line_still_starts_the_receiver(monkeypatch: pytest.MonkeyPatch) -> None:
    import capture.__main__ as cli

    seen = {}
    monkeypatch.setattr(cli, "run", lambda args: seen.update(vars(args)))
    cli.main(["--receive"])
    assert seen["receive"] is True and seen["gaze"] is False and seen["dwell_seconds"] == 8.0

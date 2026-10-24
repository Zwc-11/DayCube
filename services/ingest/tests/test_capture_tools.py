"""Opera history import, the sign-in entry and the eye-tracker supervisor."""

import json
import random
import sqlite3
import sys
import time
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from capture import autostart
from capture import history as history_module
from capture.history import EPOCH, Exclusions, _PauseIndex, import_history, paused_spans
from capture.supervise import GazeSupervisor
from ingest.vault import Vault

TORONTO = ZoneInfo("America/Toronto")


def chromium_time(iso: str) -> int:
    return int((datetime.fromisoformat(iso) - EPOCH).total_seconds() * 1_000_000)


def history_file(path: Path, visits: list[tuple]) -> Path:
    """A History file with Chromium's columns: (id, url, title, time, transition, duration s, from)."""
    path.parent.mkdir(parents=True, exist_ok=True)
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
    history = history_file(tmp_path / "Opera Stable" / "History", [
        (1, "https://arxiv.org/abs/2406.01234", "A paper", "2026-10-21T03:30:00+00:00", 0x30000001, 42.5, 0),
        (2, "https://arxiv.org/pdf/2406.01234", "PDF", "2026-10-21T03:31:00+00:00", 0x00000000, 0, 1),
        (3, "https://ads.example.com/frame", "", "2026-10-21T03:31:01+00:00", 0x00000003, 0, 0),
        (4, "https://www.mybank.com/login", "Bank", "2026-10-21T15:00:00+00:00", 0x02000001, 0, 0),
        (5, "chrome://settings/", "Settings", "2026-10-21T15:01:00+00:00", 0x00000001, 0, 0),
        (6, "https://news.ycombinator.com/", "HN", "2026-10-21T16:00:00+00:00", 0x80000008, 3, 0),
        (7, "https://example.org/reset", "Reset your password", "2026-10-21T16:05:00+00:00", 0x00000000, 0, 0),
    ])  # fmt: skip
    exclusions = Exclusions(domains=["mybank.com"], title_patterns=["(?i)password"])
    assert import_history(vault, TORONTO, [history], exclusions=exclusions) == {"2026-10-20": 2, "2026-10-21": 1}
    evening = [json.loads(line) for line in (vault.root / "capture/2026-10-20/history.jsonl").open()]
    first, second = evening
    assert (first["transition"], first["qualifiers"], first["duration_s"], first["from"]) == (
        "typed", ["chain_start", "chain_end"], 42.5, None
    )  # fmt: skip
    assert (second["transition"], second["from"], second["source"]) == ("link", first["id"], "Opera Stable")
    assert first["t"] == "2026-10-20T23:30:00-04:00"  # 03:30 UTC is the previous evening in Toronto
    later = json.loads((vault.root / "capture/2026-10-21/history.jsonl").read_text())
    assert (later["transition"], later["qualifiers"]) == ("reload", ["server_redirect"])
    # Importing again rewrites those days rather than appending duplicates.
    again = import_history(vault, TORONTO, [history], since=date(2026, 10, 21), exclusions=exclusions)
    assert again == {"2026-10-21": 1}
    assert len((vault.root / "capture/2026-10-20/history.jsonl").read_text().splitlines()) == 2


def test_history_import_leaves_out_paused_and_excluded_visits_and_follows_deletions(
    vault: Vault, tmp_path: Path
) -> None:
    history = history_file(tmp_path / "History", [
        (1, "https://a.example/", "A", "2026-10-21T14:00:00+00:00", 0, 0, 0),
        (2, "https://b.example/", "B", "2026-10-21T15:30:00+00:00", 0, 0, 0),  # during the pause
        (3, "https://c.example/", "C", "2026-10-22T14:00:00+00:00", 0, 0, 0),
    ])  # fmt: skip
    vault.append_lines(vault.root / "capture/pauses.jsonl", [
        {"t": "2026-10-21T11:00:00-04:00", "paused": True},
        {"t": "2026-10-21T12:00:00-04:00", "paused": False},
    ])  # fmt: skip
    assert import_history(vault, TORONTO, [history]) == {"2026-10-21": 1, "2026-10-22": 1}
    assert "b.example" not in (vault.root / "capture/2026-10-21/history.jsonl").read_text()
    # The extension now excludes c.example: its only day loses its file on the next import.
    vault.write_json(vault.root / "capture/exclusions.json", {"domains": ["c.example"]})
    exclusions = Exclusions.for_vault(vault, path=tmp_path / "none.yaml")
    assert import_history(vault, TORONTO, [history], exclusions=exclusions) == {"2026-10-21": 1, "2026-10-22": 0}
    assert not (vault.root / "capture/2026-10-22/history.jsonl").exists()


def test_two_browsers_on_one_day_are_merged_not_overwritten(vault: Vault, tmp_path: Path) -> None:
    stable = history_file(
        tmp_path / "Opera Stable" / "History", [(1, "https://a.example/", "A", "2026-10-21T14:00:00+00:00", 0, 0, 0)]
    )
    gx = history_file(
        tmp_path / "Opera GX Stable" / "History", [(1, "https://b.example/", "B", "2026-10-21T15:00:00+00:00", 0, 0, 0)]
    )
    assert import_history(vault, TORONTO, [stable, gx]) == {"2026-10-21": 2}
    visits = [json.loads(line) for line in (vault.root / "capture/2026-10-21/history.jsonl").open()]
    assert [v["source"] for v in visits] == ["Opera Stable", "Opera GX Stable"]
    assert len({v["id"] for v in visits}) == 2  # the same visit number in two files is two visits


@pytest.mark.parametrize("dense", [False, True])
def test_pause_index_matches_interval_union_in_any_order(dense: bool) -> None:
    rng = random.Random(7)
    origin = datetime(2026, 10, 24, tzinfo=UTC)
    spans = []
    for _ in range(80):
        start = rng.randrange(200)
        end = rng.randrange(200) if dense else start + rng.randrange(-2, 3)
        spans.append((origin + timedelta(seconds=start), origin + timedelta(seconds=end)))
    spans.extend([(origin, origin), (origin, origin + timedelta(seconds=10))] * 2)
    rng.shuffle(spans)
    index = _PauseIndex(spans)
    for i in range(-1, 402):
        t = (origin + timedelta(seconds=i / 2)).astimezone(TORONTO)
        assert index.contains(t) == any(start <= t < end for start, end in spans)
    for start, end in spans:
        assert index.contains(start) == any(a <= start < b for a, b in spans)
        assert index.contains(end) == any(a <= end < b for a, b in spans)
    assert _PauseIndex([]).contains(origin) is False


def test_history_pause_boundaries_overlap_and_backwards_clock(vault: Vault, tmp_path: Path) -> None:
    times = ["12:59:59.999999", "13:00:00", "14:30:00", "14:59:59.999999", "15:00:00", "16:00:00"]
    history = history_file(tmp_path / "History", [
        (i, "https://synthetic.example/", "Synthetic", f"2026-10-21T{t}+00:00", 0, 0, 0)
        for i, t in enumerate(times, 1)
    ])  # fmt: skip
    # A clock correction can put later log entries before earlier ones. Pauses overlap;
    # the backwards/empty intervals still exclude nothing, as in the original union test.
    vault.append_lines(vault.root / "capture/pauses.jsonl", [
        {"t": "2026-10-21T14:00:00+00:00", "paused": True},
        {"t": "2026-10-21T15:00:00+00:00", "paused": False},
        {"t": "2026-10-21T09:00:00-04:00", "paused": True},
        {"t": "2026-10-21T10:30:00-04:00", "paused": False},
        {"t": "2026-10-21T16:00:00+00:00", "paused": True},
        {"t": "2026-10-21T15:30:00+00:00", "paused": False},
    ])  # fmt: skip
    assert import_history(vault, TORONTO, [history]) == {"2026-10-21": 3}
    records = [json.loads(line) for line in (vault.root / "capture/2026-10-21/history.jsonl").read_text().splitlines()]
    assert [int(row["id"].rsplit("-", 1)[1]) for row in records] == [1, 5, 6]
    assert records[1]["t"] == "2026-10-21T11:00:00-04:00"


def test_open_pause_is_indexed_until_the_import_snapshot(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 10, 24, 14, tzinfo=UTC)
    vault.append_lines(vault.root / "capture/pauses.jsonl", [
        {"t": "2026-10-24T09:00:00-04:00", "paused": True},
    ])  # fmt: skip
    monkeypatch.setattr(
        history_module, "datetime", SimpleNamespace(fromisoformat=datetime.fromisoformat, now=lambda zone: now)
    )
    spans = paused_spans(vault)
    assert spans == [(datetime(2026, 10, 24, 13, tzinfo=UTC), now)]
    index = _PauseIndex(spans)
    assert index.contains(now - timedelta(microseconds=1))
    assert not index.contains(now)


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


def test_a_hook_path_with_spaces_survives_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    import capture.__main__ as cli

    monkeypatch.setattr(cli.os, "name", "nt")
    assert cli._split(r'"C:\Program Files\Hook\hook.exe" --flag "two words"') == [
        r"C:\Program Files\Hook\hook.exe", "--flag", "two words"
    ]  # fmt: skip


def test_old_capture_command_line_still_starts_the_receiver(monkeypatch: pytest.MonkeyPatch) -> None:
    import capture.__main__ as cli

    seen = {}
    monkeypatch.setattr(cli, "run", lambda args: seen.update(vars(args)))
    cli.main(["--receive"])
    assert seen["receive"] is True and seen["gaze"] is False and seen["dwell_seconds"] == 8.0

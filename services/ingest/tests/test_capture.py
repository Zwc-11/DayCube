"""The capture receiver: browser records, gaze, dwells, pause."""

import json
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from capture.api import create_capture_app
from fastapi.testclient import TestClient
from ingest.config import Settings
from ingest.vault import Vault

GEOMETRY = {
    "vx": 0.0, "vy": 100.0, "scale": 1.0, "inner_w": 1920.0, "inner_h": 980.0, "screen_w": 1920.0,
    "screen_h": 1080.0, "primary": True, "exact": True, "scroll_y": 0.0, "scroll_h": 4000.0,
}  # fmt: skip
TAB = {"tab": 7, "window": 1, "url": "https://example.com/paper", "title": "A paper"}


def stamp(seconds: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def record(kind: str, seconds: float = 0.0, **fields: object) -> dict:
    return {"id": uuid.uuid4().hex, "t": stamp(seconds), "kind": kind, **fields}


def settings(vault: Vault) -> Settings:
    return Settings(vault_root=vault.root, _env_file=None)


def logged(vault: Vault, name: str) -> list[dict]:
    return [json.loads(line) for path in sorted((vault.root / "capture").glob(f"*/{name}")) for line in path.open()]


def test_receiver_starts_paused_requires_auth_and_remembers_a_pause(vault: Vault, auth: dict[str, str]) -> None:
    with TestClient(create_capture_app(settings(vault))) as client:
        assert client.get("/status").json()["paused"] is True
        assert client.post("/resume").status_code == 401
        assert client.post("/events", json=[record("idle", state="active")], headers=auth).status_code == 409
        assert client.post("/resume", headers=auth).json()["paused"] is False
    with TestClient(create_capture_app(settings(vault), receive=True)) as client:
        assert client.get("/status").json()["paused"] is False
        client.post("/pause", headers=auth)
    with TestClient(create_capture_app(settings(vault), receive=True)) as client:
        assert client.get("/status").json()["paused"] is True  # a restart never undoes a pause


def test_pause_and_resume_never_replay_old_tracking_telemetry(vault: Vault, auth: dict[str, str]) -> None:
    state = {"tracking": True, "monitor": [1920, 1080], "fps": 29.8, "target_fps": 30.0, "face": 1.0}
    with TestClient(create_capture_app(settings(vault), receive=True)) as client:
        assert client.post("/gaze", json={"state": state, "fixations": []}, headers=auth).status_code == 200
        assert client.get("/status").json()["gaze"]["target_fps"] == 30.0
        paused = client.post("/pause", headers=auth).json()["gaze"]
        assert paused["tracking"] is False and paused["reason"] == "paused"
        assert paused["fps"] is None and paused["face"] is None
        assert client.post("/gaze", json={"state": state, "fixations": []}, headers=auth).status_code == 409
        resumed = client.post("/resume", headers=auth).json()["gaze"]
        assert resumed["tracking"] is False
        assert client.post("/gaze", json={"state": state, "fixations": []}, headers=auth).status_code == 200
        assert client.get("/status").json()["gaze"]["tracking"] is True


def test_browser_records_are_stored_by_kind_once_each(vault: Vault, auth: dict[str, str]) -> None:
    page = record("page", transition="typed", qualifiers=["from_address_bar"], **TAB)
    blocks = record(
        "blocks",
        doc="doc-1",
        geometry=GEOMETRY,
        blocks=[{"id": "b1", "tag": "p", "text": "Hello", "rect": [0, 0, 9, 9]}],
        **TAB,
    )
    batch = [
        page,
        record("search", engine="google", query="kalman filter gaze", **TAB),
        record("click", href="https://example.com/next", text="Next", **TAB),
        record("selection", text="trades compute for memory", copied=True, **TAB),
        record("media", state="playing", position_s=12.5, duration_s=600, **TAB),
        record("focus", tab=None),
        record("view", doc="doc-1", geometry=GEOMETRY, **TAB),
        blocks,
        record("media", state="rewinding", position_s=1, **TAB),  # invalid
        record("idle", -2 * 86400, state="idle"),  # too old
    ]
    with TestClient(create_capture_app(settings(vault), receive=True)) as client:
        body = client.post("/events", json=batch, headers=auth).json()
        assert (body["accepted"], body["duplicates"], len(body["rejected"])) == (8, 0, 2)
        assert body["rejected"][0]["reason"].startswith("media.state")
        again = client.post("/events", json=[page, blocks], headers=auth).json()
        assert (again["accepted"], again["duplicates"]) == (0, 2)
        assert client.get("/status").json()["browser"]["last"] is not None
    kinds = [line["kind"] for line in logged(vault, "browser.jsonl")]
    assert kinds == ["page", "search", "click", "selection", "media", "focus"]  # the view heartbeat is live only
    assert [line["doc"] for line in logged(vault, "blocks.jsonl")] == ["doc-1"]


def test_a_stare_at_a_paragraph_becomes_a_dwell_signal_and_hook(
    vault: Vault, auth: dict[str, str], tmp_path: Path
) -> None:
    out = tmp_path / "hook.json"
    hook = [
        sys.executable,
        "-c",
        f"import sys, pathlib; pathlib.Path({str(out)!r}).write_bytes(sys.stdin.buffer.read())",
    ]
    text = "Gradient checkpointing trades compute for memory."
    blocks = [{"id": "b1", "tag": "p", "text": text, "rect": [100, 200, 800, 120]}]
    start = datetime.now(UTC) - timedelta(seconds=12)
    fixations = [
        {
            "id": uuid.uuid4().hex,
            "t": (start + timedelta(seconds=0.5 * i)).isoformat(),
            "t_end": (start + timedelta(seconds=0.5 * i + 0.5)).isoformat(),
            "x": 500 / 1920,
            "y": (100 + 260) / 1080,
            "samples": 8,
        }
        for i in range(18)  # 9 s on the paragraph
    ]
    state = {"tracking": True, "calibrated": True, "error_px": 120.0, "monitor": [1920, 1080], "fps": 15.0}
    with TestClient(create_capture_app(settings(vault), receive=True, on_dwell=hook)) as client:
        snapshot = record("blocks", -12, doc="doc-1", geometry=GEOMETRY, blocks=blocks, **TAB)
        assert client.post("/events", json=[snapshot], headers=auth).json()["accepted"] == 1
        body = client.post("/gaze", json={"state": state, "fixations": fixations}, headers=auth).json()
        assert body == {"accepted": 18, "duplicates": 0, "stale": 0, "live": False, "ring": False, "offset_grid": None}
        status = client.get("/status").json()["gaze"]
        assert status["tracking"] is True and status["error_px"] == 120.0
        signals = client.post("/events", json=[], headers=auth).json()["signals"]
        assert [(s["kind"], s["block"], s["dwell_s"], s["text"]) for s in signals] == [("dwell", "b1", 8.0, text)]
        assert client.post("/events", json=[], headers=auth).json()["signals"] == []  # delivered once
        client.post("/pause", headers=auth)  # ends the dwell
        assert client.post("/gaze", json={"state": state, "fixations": []}, headers=auth).status_code == 409
    gaze = logged(vault, "gaze.jsonl")
    assert len(gaze) == 18 and {(g["block"], g["doc"]) for g in gaze} == {("b1", "doc-1")}
    assert gaze[0]["page"] == [500.0, 260.0]
    dwells = logged(vault, "attention.jsonl")
    assert [(d["phase"], d["dwell_s"]) for d in dwells] == [("start", 8.0), ("end", 9.0)]
    assert dwells[0]["url"] == TAB["url"] and dwells[0]["text"] == text
    deadline = time.monotonic() + 10
    while not (out.exists() and out.stat().st_size) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert json.loads(out.read_text())["block"] == "b1"


def test_a_record_whose_write_failed_is_stored_when_resent(
    vault: Vault, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    item = record("click", href="https://example.com/next", text="Next", **TAB)
    app = create_capture_app(settings(vault), receive=True)
    append = app.state.receiver.vault.append_lines
    with TestClient(app, raise_server_exceptions=False) as client:
        monkeypatch.setattr(
            app.state.receiver.vault, "append_lines", lambda *_: (_ for _ in ()).throw(PermissionError())
        )
        assert client.post("/events", json=[item], headers=auth).status_code == 500  # a file locked by a scanner
        monkeypatch.setattr(app.state.receiver.vault, "append_lines", append)
        assert client.post("/events", json=[item], headers=auth).json()["accepted"] == 1
    assert [line["id"] for line in logged(vault, "browser.jsonl")] == [item["id"]]


def test_pauses_and_exclusions_are_kept_for_the_history_import(vault: Vault, auth: dict[str, str]) -> None:
    with TestClient(create_capture_app(settings(vault), receive=True)) as client:
        client.post("/pause", headers=auth)
        client.post("/pause", headers=auth)  # no change, no entry
        client.post("/resume", headers=auth)
        domains = {"domains": ["MyBank.com", "health.example.org"]}
        assert client.post("/exclusions", json=domains, headers=auth).json() == {"domains": 2}
        assert client.post("/exclusions", json=domains).status_code == 401
    pauses = [json.loads(line) for line in (vault.root / "capture/pauses.jsonl").open()]
    assert [p["paused"] for p in pauses] == [True, False]
    saved = json.loads((vault.root / "capture/exclusions.json").read_text())
    assert saved == {"domains": ["health.example.org", "mybank.com"]}


def test_block_texts_survive_a_receiver_restart(vault: Vault, auth: dict[str, str]) -> None:
    text = "Gradient checkpointing trades compute for memory."
    blocks = [{"id": "b1", "tag": "p", "text": text, "words": 6, "rect": [100, 200, 800, 120]}]
    with TestClient(create_capture_app(settings(vault), receive=True)) as client:
        client.post(
            "/events", json=[record("blocks", doc="doc-1", geometry=GEOMETRY, blocks=blocks, **TAB)], headers=auth
        )
    restarted = create_capture_app(settings(vault), receive=True)
    assert restarted.state.receiver.attention.texts[("doc-1", "b1")] == ("p", text, 6)


def test_clicks_measure_live_accuracy_and_health_is_logged(vault: Vault, auth: dict[str, str]) -> None:
    """Six clicks, each with the tracker's fixation 40 px below it: the receiver learns the offset,
    reports it in /status, and writes a health line with the tracker's own figures."""
    state = {
        "tracking": True, "calibrated": True, "error_px": 69.1, "monitor": [1920, 1080], "fps": 29.5,
        "target_fps": 30.0, "face": 1.0, "coverage": 0.9, "noise_px": 21.0,
    }  # fmt: skip
    start = datetime.now(UTC) - timedelta(seconds=30)
    clicks, fixations = [], []
    for i in range(6):
        x, y = 200 + 250 * i, 150 + 100 * i
        t = start + timedelta(seconds=4 * i)
        clicks.append(
            {**record("click", href=None, text="Next", x=x, y=y, geometry=GEOMETRY, **TAB), "t": t.isoformat()}
        )
        for begin, end, dx in ((-0.5, 0.2, 0), (0.3, 0.9, 300)):
            fixations.append({
                "id": uuid.uuid4().hex, "t": (t + timedelta(seconds=begin)).isoformat(),
                "t_end": (t + timedelta(seconds=end)).isoformat(),
                "x": (x + dx) / 1920, "y": (100 + y + 40) / 1080, "samples": 20,
            })  # fmt: skip
    with TestClient(create_capture_app(settings(vault), receive=True)) as client:
        assert client.post("/events", json=clicks, headers=auth).json()["accepted"] == 6
        assert (
            client.post("/gaze", json={"state": state, "fixations": fixations}, headers=auth).json()["accepted"] == 12
        )
        accuracy = client.get("/status").json()["accuracy"]
    assert accuracy["clicks"] == 6 and accuracy["live_error_px"] == 40.0
    assert accuracy["corrected_error_px"] < 15 and accuracy["offset_px"][1] > 30  # each click from the other five
    assert accuracy["planned_error_px"] == accuracy["corrected_error_px"]
    stored = [line for line in logged(vault, "browser.jsonl") if line["kind"] == "click"]
    assert stored[0]["x"] == 200 and stored[0]["geometry"]["scale"] == 1.0
    (health,) = logged(vault, "health.jsonl")
    assert health["gaze"]["coverage"] == 0.9 and health["accuracy"]["clicks"] == 6


def test_the_line_being_read_is_logged_shown_and_recorded(vault: Vault, auth: dict[str, str]) -> None:
    """A three-line paragraph read line by line: each fixation in gaze.jsonl names the line,
    /status shows the current one, and leaving the page writes the paragraph to reading.jsonl."""
    state = {"tracking": True, "calibrated": True, "error_px": 30.0, "monitor": [1920, 1080]}
    start = datetime.now(UTC) - timedelta(seconds=7)
    texts = ["People remember places better", "than lists, and a walk through a", "street is an index."]
    lines = [{"rect": [100, 200 + 28.8 * i, 700 if i < 2 else 300, 20], "text": text} for i, text in enumerate(texts)]
    paragraph = {
        "id": "p1",
        "tag": "p",
        "text": " ".join(texts),
        "words": 17,
        "rect": [100, 200, 700, 77.6],
        "lines": lines,
    }
    blocks = {**record("blocks", doc="doc-1", geometry=GEOMETRY, blocks=[paragraph], **TAB), "t": start.isoformat()}
    fixations, truth, t = [], [], start + timedelta(seconds=0.5)
    for i, line in enumerate(lines):
        left, top, width, height = line["rect"]
        for k in range(6):
            fixations.append({
                "id": uuid.uuid4().hex, "t": t.isoformat(), "t_end": (t + timedelta(seconds=0.25)).isoformat(),
                "x": (left + width * (k + 0.5) / 6) / 1920, "y": (100 + top + height / 2 + 8) / 1080, "samples": 8,
            })  # fmt: skip
            truth.append(i)
            t += timedelta(seconds=0.3)
    with TestClient(create_capture_app(settings(vault), receive=True)) as client:
        assert client.post("/events", json=[blocks], headers=auth).json()["accepted"] == 1
        assert client.post("/gaze", json={"state": state, "fixations": fixations}, headers=auth).status_code == 200
        status = client.get("/status").json()
        reading, decoding = status["reading"], status["line_decoding"]
        assert reading["block"] == "p1" and reading["line"] == 2 and reading["text"] == texts[2]
        assert decoding["fixations"] == 18 and decoding["on_page"] == 18 and decoding["on_line"] >= 16
        assert decoding["page"] == {"host": "example.com", "blocks": 1, "with_lines": 1}
        placed = [f["line"] for f in logged(vault, "gaze.jsonl")]
        assert (
            sum(p is not None and (p["block"], p["line"]) == ("p1", i) for p, i in zip(placed, truth, strict=True))
            >= 16
        )
        assert logged(vault, "reading.jsonl") == []  # still on the paragraph
        other = {**record("blocks", doc="doc-2", geometry=GEOMETRY, blocks=[], **TAB), "t": t.isoformat()}
        assert client.post("/events", json=[other], headers=auth).json()["accepted"] == 1
    (read,) = logged(vault, "reading.jsonl")
    assert read["kind"] == "read" and read["block"] == "p1" and read["url"] == TAB["url"]
    assert read["line_count"] == 3 and read["coverage"] == 1.0
    assert [entry["text"] for entry in read["lines"]] == texts
    assert logged(vault, "attention.jsonl") == []  # a read is not a dwell
    # A restarted receiver has the lines of pages already open: they are sent once per page load.
    restarted = create_capture_app(settings(vault), receive=True)
    assert [line.text for line in restarted.state.receiver.attention.layouts[("doc-1", "p1")]] == texts


def test_the_doctor_judges_capture_from_what_it_recorded(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    from zoneinfo import ZoneInfo

    from capture import doctor

    now = datetime.now(UTC)
    zone = ZoneInfo("America/Toronto")
    day = vault.root / "capture" / now.astimezone(zone).date().isoformat()
    day.mkdir(parents=True)
    (day / "attention.jsonl").write_text('{"phase": "start"}\n{"phase": "end"}\n', encoding="utf-8")
    (day / "gaze.jsonl").write_text('{"x": 1}\nnot json\n{"x": 2', encoding="utf-8")  # last line still being written
    (day / "reading.jsonl").write_text(
        '{"lines": [{"line": 0}, {"line": 1}]}\n{"lines": [{"line": 3}]}\n', encoding="utf-8"
    )
    status = {
        "paused": False, "browser": {"last": (now - timedelta(minutes=2)).isoformat()},
        "gaze": {"tracking": True, "fps": 29.4, "target_fps": 30.0, "face": 1.0, "coverage": 0.88, "noise_px": 24.0,
                 "error_px": 69.1, "monitor": [2560, 1440], "model": "head", "head_moved_mm": 64.0,
                 "head_turned_deg": 8.2,
                 "timing_ms": {"camera_wait": 15.0, "landmarks": 12.4, "model": 0.3, "tracking": 0.05,
                               "network": 0.1, "frame": 12.9, "live_post": 0.6}},
        "accuracy": {"clicks": 7, "rejected": 0, "live_error_px": 120.0, "corrected_error_px": 58.0},
        "reading": None, "code": doctor.code_stamp(),
    }  # fmt: skip
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    results = {name: (verdict, detail) for verdict, name, detail in doctor.checks(status, vault.root, zone, now, None)}
    assert results["frame time"] == (
        doctor.PASS,
        "12.9 ms of 33: face landmarks 12.4, gaze model 0.3, fixations 0.1, network 0.1, "
        "live point 0.6 on its own thread",
    )
    assert results["gaze model"] == (
        doctor.PASS,
        "head-aware: follows head movement; head now 6 cm and 8 degrees from where it was at calibration",
    )
    eyetrax = status | {"gaze": status["gaze"] | {"model": "ridge", "head_moved_mm": None}}
    verdicts = {name: v for v, name, _ in doctor.checks(eyetrax, vault.root, zone, now, None)}
    assert verdicts["gaze model"] == doctor.WARN
    assert results["receiver"][0] == doctor.PASS and results["fixations"] == (doctor.PASS, "88% of gaze, noise 24 px")
    assert results["calibration"][0] == doctor.PASS and results["live accuracy"][0] == doctor.PASS
    assert results["today's logs"][0] == doctor.FAIL and "1 malformed" in results["today's logs"][1]
    assert results["dwells today"] == (doctor.INFO, "1")
    assert results["reading today"] == (doctor.INFO, "2 blocks read, 3 lines") and "receiver code" not in results
    # Why no line is decoded, from the receiver's record of where the last fixations went.
    page = {"host": "learn.uwaterloo.ca", "blocks": 14, "with_lines": 0}
    for decoding, verdict, reason in (
        ({"fixations": 300, "on_page": 12, "on_line": 0, "page": page}, doctor.WARN, "not landing on pages"),
        ({"fixations": 300, "on_page": 280, "on_line": 0, "page": page}, doctor.WARN, "sent no lines"),
        (
            {"fixations": 95, "on_page": 7, "on_line": 4, "no_page": 58, "outside": 30, "page": page},
            doctor.WARN,
            "58 with no browser page on screen",
        ),
        (
            {"fixations": 300, "on_page": 280, "on_line": 250, "page": page | {"with_lines": 14}},
            doctor.PASS,
            "250 on a line",
        ),
    ):
        found = {
            name: (v, d)
            for v, name, d in doctor.checks(status | {"line_decoding": decoding}, vault.root, zone, now, None)
        }
        assert found["line reading"][0] == verdict and reason in found["line reading"][1]
    worse = status | {"accuracy": {"clicks": 7, "rejected": 9, "live_error_px": 300.0, "corrected_error_px": 260.0}}
    results = {name: verdict for verdict, name, _ in doctor.checks(worse, vault.root, zone, now, None)}
    assert results["live accuracy"] == doctor.FAIL and results["clicks"] == doctor.WARN
    assert doctor.checks(None, vault.root, zone, now, None)[0][0] == doctor.FAIL
    # A receiver or tracker started before a pull runs the old code: say so.
    old = {k: v for k, v in status.items() if k not in ("reading", "code")} | {
        "gaze": status["gaze"] | {"coverage": None, "noise_px": None}
    }
    results = {name: verdict for verdict, name, _ in doctor.checks(old, vault.root, zone, now, None)}
    assert results["receiver code"] == doctor.FAIL and results["tracker code"] == doctor.WARN


def test_the_live_gaze_point_streams_only_while_the_ring_is_shown(vault: Vault, auth: dict[str, str]) -> None:
    """The extension asks /live for its page; the next /gaze reply tells the tracker to stream,
    and each live point comes back in that page's viewport px, corrected like fixations."""
    state = {"tracking": True, "calibrated": True, "error_px": 30.0, "monitor": [1920, 1080]}
    page = {**record("view", doc="doc-1", geometry=GEOMETRY, **TAB)}
    with TestClient(create_capture_app(settings(vault), receive=True)) as client:
        assert client.post("/events", json=[page], headers=auth).json()["accepted"] == 1
        assert client.post("/gaze", json={"state": state, "fixations": []}, headers=auth).json()["live"] is False
        assert client.get("/live", params={"doc": "doc-1"}).status_code == 401
        view = client.get("/live", params={"doc": "doc-1"}, headers=auth).json()
        assert view == {"point": None, "reading": None, "fixation": None}  # nothing streamed yet
        assert client.post("/gaze", json={"state": state, "fixations": []}, headers=auth).json()["live"] is True
        point = {"t": stamp(), "x": 500 / 1920, "y": (100 + 260) / 1080}
        assert client.post("/gaze/live", json=point, headers=auth).json() == {"ok": True}
        view = client.get("/live", params={"doc": "doc-1"}, headers=auth).json()
        assert (view["point"]["x"], view["point"]["y"], view["point"]["inside"]) == (500.0, 260.0, True)
        assert client.get("/live", params={"doc": "another-doc"}, headers=auth).json()["point"] is None
        ring = client.get("/status").json()["ring"]
        # 3 asks, 1 answered with a live point (the first came before any point, the last was for another page)
        assert ring == {"points_per_s": 0.1, "asks_per_s": 0.3, "live_share": 0.33, "ever_asked": True}
        client.post("/pause", headers=auth)
        assert client.post("/gaze/live", json=point, headers=auth).status_code == 409
    assert not any(path.name == "live.jsonl" for path in (vault.root / "capture").rglob("*"))  # never stored


def test_the_doctor_says_which_side_of_the_gaze_ring_is_missing(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ring that jumps once a second is either an extension that never asks for the live point
    (older than 0.7.0) or a tracker that does not stream it; doctor tells them apart."""
    from zoneinfo import ZoneInfo

    from capture import doctor

    now = datetime.now(UTC)
    zone = ZoneInfo("America/Toronto")
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    base = {
        "paused": False, "browser": {"last": now.isoformat()}, "reading": None,
        "gaze": {"tracking": True, "fps": 30.0, "target_fps": 30.0, "face": 1.0, "coverage": 0.8, "noise_px": 20.0},
    }  # fmt: skip

    def ring(**values: object) -> tuple[str, str]:
        status = base | {
            "ring": {"points_per_s": 0.0, "asks_per_s": 0.0, "live_share": None, "ever_asked": False} | values
        }
        return next((v, d) for v, name, d in doctor.checks(status, vault.root, zone, now, None) if name == "gaze ring")

    verdict, detail = ring()
    assert verdict == doctor.INFO and "older than 0.7.0" in detail
    verdict, detail = ring(ever_asked=True, asks_per_s=9.8, live_share=0.0)
    assert verdict == doctor.WARN and "sends 0 live points/s" in detail
    verdict, detail = ring(ever_asked=True, asks_per_s=9.8, points_per_s=10.1, live_share=0.1)
    assert verdict == doctor.WARN and "10%" in detail
    assert ring(ever_asked=True, asks_per_s=9.8, points_per_s=10.1, live_share=0.97) == (
        doctor.PASS,
        "10 live points/s, the page asks 10 times/s",
    )


def test_the_doctor_shows_why_the_tracker_is_not_running(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    from zoneinfo import ZoneInfo

    from capture import doctor

    now = datetime.now(UTC)
    zone = ZoneInfo("America/Toronto")
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    logs = vault.root / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "gaze.log").write_text(
        "2026-10-23 22:18:01,100 gaze INFO tracking with camera 0 at up to 30.0 fps\n"
        "2026-10-23 22:18:02,200 gaze ERROR the tracker stopped on an error\n"
        "Traceback (most recent call last):\n",
        encoding="utf-8",
    )
    (logs / "capture.log").write_text(
        "2026-10-23 22:18:03,300 capture.gaze WARNING eye tracker exited with code 1; restarting in 10 s\n",
        encoding="utf-8",
    )
    status = {"paused": False, "browser": {"last": now.isoformat()}, "reading": None,
              "gaze": {"tracking": False, "reason": "not running"}}  # fmt: skip
    found = {name: (v, d) for v, name, d in doctor.checks(status, vault.root, zone, now, None)}
    assert found["eye tracker"] == (
        doctor.FAIL,
        "not tracking: not running. Last from the tracker's logs: "
        "WARNING eye tracker exited with code 1; restarting in 10 s",
    )
    paused = status | {"gaze": {"tracking": False, "reason": "paused"}}
    found = {name: d for _, name, d in doctor.checks(paused, vault.root, zone, now, None)}
    assert "toolbar button" in found["eye tracker"]


def test_the_librarian_keeps_the_days_notes_current(vault: Vault) -> None:
    from zoneinfo import ZoneInfo

    from capture.librarian import Librarian
    from conftest import CAPTURE_DAY, capture_session

    capture_session(vault)
    librarian = Librarian(vault.root, "America/Toronto")
    noon = datetime(2026, 10, 22, 12, 0, tzinfo=ZoneInfo("America/Toronto"))
    assert librarian.step(noon) == [CAPTURE_DAY]
    daily = vault.root / "notes" / "Daily" / f"{CAPTURE_DAY}.md"
    assert "CS 136" in daily.read_text(encoding="utf-8")
    assert librarian.step(noon) == []  # nothing new
    with (vault.root / "capture" / CAPTURE_DAY / "browser.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"id": "s9-0000001", "t": f"{CAPTURE_DAY}T11:00:00-04:00", "kind": "search", "tab": 1,
                                 "window": 1, "url": "https://www.google.com/search?q=b", "title": "",
                                 "engine": "google", "query": "robin hood hashing"}) + "\n")  # fmt: skip
    assert librarian.step(noon) == [CAPTURE_DAY]
    assert "robin hood hashing" in daily.read_text(encoding="utf-8")
    tomorrow = datetime(2026, 10, 23, 9, 0, tzinfo=ZoneInfo("America/Toronto"))
    assert librarian.step(tomorrow) == []  # yesterday unchanged, today has no capture yet


def test_the_tracker_draws_the_ring_over_every_window_when_asked(vault: Vault, auth: dict[str, str]) -> None:
    """The extension's option (or `capture ring on`) is remembered; the tracker learns it from the
    /gaze reply, with the click correction as a grid; pages then draw no ring of their own."""
    from capture.anchors import Anchor

    overlay = {"running": True, "shown": True, "fps": 143.0, "moves_per_s": 120.0, "refresh_hz": 144.0}
    state = {"tracking": True, "calibrated": True, "error_px": 30.0, "monitor": [1920, 1080], "overlay": overlay}
    page = {**record("view", doc="doc-1", geometry=GEOMETRY, **TAB)}
    with TestClient(create_capture_app(settings(vault), receive=True)) as client:
        assert client.get("/display").json() == {"gaze_ring": False, "native": False}
        assert client.post("/display", json={"gaze_ring": True}).status_code == 401
        client.post("/events", json=[page], headers=auth)
        client.get("/live", params={"doc": "doc-1"}, headers=auth)  # a page asks for the point
        reply = client.post("/gaze", json={"state": state, "fixations": []}, headers=auth).json()
        assert reply["ring"] is False and reply["live"] is True
        assert client.post("/display", json={"gaze_ring": True}, headers=auth).json()["gaze_ring"] is True
        reply = client.post("/gaze", json={"state": state, "fixations": []}, headers=auth).json()
        assert reply["ring"] is True and reply["offset_grid"] is None  # no clicks to correct from yet
        # The tracker draws it now: no live points over HTTP, and the page draws nothing.
        reply = client.post("/gaze", json={"state": state, "fixations": []}, headers=auth).json()
        assert reply["live"] is False
        view = client.get("/live", params={"doc": "doc-1"}, headers=auth).json()
        assert view["native"] is True and view["point"] is None and view["fixation"] is None
        assert client.get("/status").json()["display"] == {"gaze_ring": True, "native": True}
        # Clicks taught the receiver the tracker reads 40 px right of the centre: the grid carries it.
        receiver = client.app.state.receiver
        receiver.attention.anchors.anchors.append(Anchor(datetime.now(UTC).timestamp(), 960.0, 540.0, 40.0, 0.0))
        grid = client.post("/gaze", json={"state": state, "fixations": []}, headers=auth).json()["offset_grid"]
        assert (grid["cols"], grid["rows"], len(grid["dx"])) == (9, 5, 45)
        centre, corner = grid["dx"][2 * 9 + 4], grid["dx"][0]
        assert 0 < corner < centre <= 40 / 1920 and all(v == 0 for v in grid["dy"])
        client.post("/pause", headers=auth)
        assert client.get("/display").json()["native"] is False  # paused: nothing drawn
    # Remembered across restarts.
    with TestClient(create_capture_app(settings(vault), receive=True)) as client:
        assert client.get("/display").json()["gaze_ring"] is True


def test_the_doctor_reports_the_ring_over_every_window(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    from zoneinfo import ZoneInfo

    from capture import doctor

    now = datetime.now(UTC)
    zone = ZoneInfo("America/Toronto")
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    gaze = {"tracking": True, "fps": 60.0, "target_fps": 60.0, "face": 1.0, "coverage": 0.8, "noise_px": 20.0}
    asked = {"points_per_s": 0.0, "asks_per_s": 9.0, "live_share": 0.0, "ever_asked": True}

    def ring(display: dict, overlay: dict | None) -> list[tuple[str, str]]:
        status = {"paused": False, "browser": {"last": now.isoformat()}, "reading": None, "ring": asked,
                  "display": display, "gaze": gaze | ({"overlay": overlay} if overlay is not None else {})}  # fmt: skip
        return [(v, d) for v, name, d in doctor.checks(status, vault.root, zone, now, None) if name == "gaze ring"]

    drawn = {"running": True, "shown": True, "fps": 143.0, "moves_per_s": 140.0, "refresh_hz": 144.0}
    assert ring({"gaze_ring": True, "native": True}, drawn) == [
        (
            doctor.PASS,
            "drawn over every window: 143 frames/s on a 144 Hz display, moving 140 times/s between 60 gaze points/s",
        )
    ]  # fmt: skip  -- and the page ring's checks are skipped
    off = ring({"gaze_ring": False, "native": False}, None)
    assert off[0][0] == doctor.INFO and "capture ring on" in off[0][1]
    broken = ring({"gaze_ring": True, "native": False}, {"running": False, "error": "OSError: no desktop"})
    assert broken[0][0] == doctor.WARN and "OSError: no desktop" in broken[0][1]
    old = ring({"gaze_ring": True, "native": False}, None)
    assert old[0][0] == doctor.WARN and "older than this checkout" in old[0][1]


def test_the_doctor_reports_the_webcam_rate_and_how_to_get_60(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    from zoneinfo import ZoneInfo

    from capture import doctor

    now = datetime.now(UTC)
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    gaze = {"tracking": True, "fps": 29.0, "target_fps": 30.0, "face": 1.0, "coverage": 0.8, "noise_px": 20.0}

    def webcam(**values: object) -> tuple[str, str]:
        status = {"paused": False, "browser": {"last": now.isoformat()}, "reading": None, "gaze": gaze | values}
        checks = doctor.checks(status, vault.root, ZoneInfo("America/Toronto"), now, None)
        return next((v, d) for v, name, d in checks if name == "webcam")

    verdict, detail = webcam(camera_fps=30.0, camera_mode="default mode")
    assert verdict == doctor.INFO and "30 gaze points/s" in detail and "calibrate" in detail
    assert webcam(camera_fps=59.6, camera_mode="1280x720 MJPG 60 fps") == (
        doctor.PASS, "60 frames/s in 1280x720 MJPG 60 fps",
    )  # fmt: skip


def test_the_doctor_names_the_tracker_crash_and_reads_the_saved_calibration(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From the user's PC (2026-10-24): the tracker kept exiting with code 1 while doctor showed only
    the restart line, a stale 37 px calibration and the head-aware video model as EyeTrax."""
    import json as _json
    from zoneinfo import ZoneInfo

    from capture import doctor

    now = datetime.now(UTC)
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    logs = vault.root / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "gaze.log").write_text(
        "2026-10-24 11:20:01,100 gaze INFO tracking with camera 0 in 1280x720 MJPG 60 fps at up to 60 fps\n"
        "2026-10-24 11:20:09,200 gaze ERROR the tracker stopped on an error\n"
        "Traceback (most recent call last):\n"
        '  File "gaze/track.py", line 300, in state\n'
        "    drawn = sum(1 for t in self._drawn if now - t <= 1.0)\n"
        "RuntimeError: deque mutated during iteration\n",
        encoding="utf-8",
    )
    (logs / "capture.log").write_text(
        "2026-10-24 11:20:10,300 capture.gaze WARNING eye tracker exited with code 1; restarting in 5 s\n",
        encoding="utf-8",
    )
    calibration = vault.root / "calibration"
    calibration.mkdir(exist_ok=True)
    (calibration / "gaze.json").write_text(_json.dumps(
        {"t": "2026-10-24T15:18:00+00:00", "model": "head-video", "error_px": 85.0, "monitor": [2560, 1440]}
    ))  # fmt: skip
    status = {"paused": False, "browser": {"last": now.isoformat()}, "reading": None,
              "gaze": {"tracking": False, "reason": "stopped", "model": "head-video", "error_px": 37.0}}  # fmt: skip
    found = {name: (v, d) for v, name, d in doctor.checks(status, vault.root, ZoneInfo("America/Toronto"), now, None)}
    assert found["eye tracker"][0] == doctor.FAIL
    assert "(the tracker's error: RuntimeError: deque mutated during iteration)" in found["eye tracker"][1]
    assert found["gaze model"] == (doctor.PASS, "head-aware (video landmarks): follows head movement")
    assert found["calibration"] == (
        doctor.PASS, "85 px median held-out error (limit 160), saved 2026-10-24 15:18 UTC",
    )  # fmt: skip


def test_a_model_learned_from_clicks_restarts_the_click_offsets_and_doctor_reports_it() -> None:
    from capture import doctor
    from capture.attention import Attention
    from capture.records import GazeState

    attention = Attention()
    state = {"tracking": True, "monitor": [2560, 1440], "error_px": 85.0}
    attention.gaze_state(GazeState(**state, learning={"version": 1, "clicks": 40}))
    attention.anchors.anchors.append(object())  # an offset learned on update 1
    attention.gaze_state(GazeState(**state, learning={"version": 1, "clicks": 41}))
    assert len(attention.anchors.anchors) == 1  # same model: kept
    attention.gaze_state(GazeState(**state, learning={"version": 2, "clicks": 60}))
    assert len(attention.anchors.anchors) == 0  # a new model: learned again

    learning = {
        "version": 3, "clicks": 260, "skipped": {"not looking": 46, "no steady look": 12, "off the monitor": 0},
        "updated": "2026-10-24T17:40:00+00:00",
        "fit": {"accepted": True, "error_px": {"calibration": 168.0, "in_use": 130.0, "learned": 121.0}},
    }  # fmt: skip
    ((verdict, name, detail),) = doctor._learning({"tracking": True, "learning": learning})
    assert (verdict, name) == (doctor.PASS, "learning")
    assert detail == (
        "from clicks: update 3 at 17:40 UTC; on clicks it had not seen, 168.0 px with the calibration alone, "
        "121.0 px now; 260 clicks since calibration, 46 made without looking at the spot"
    )
    checks = doctor.checks(
        {"paused": False, "code": doctor.code_stamp(), "gaze": {"tracking": True, "learning": learning}},
        Path("/nonexistent"), __import__("zoneinfo").ZoneInfo("UTC"), datetime.now(UTC), None,
    )  # fmt: skip
    live = next(detail for _, name, detail in checks if name == "live accuracy")
    assert live == "0 of 5 page clicks since the last learned update; the learning line has the accuracy"
    first = {"version": 0, "clicks": 12, "skipped": {}, "fit": None}
    assert doctor._learning({"tracking": True, "learning": first})[0][0] == doctor.INFO
    assert doctor._learning({"tracking": False, "learning": first}) == []

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
        assert body == {"accepted": 18, "duplicates": 0, "stale": 0}
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

"""The eye-tracking dashboard's data (ingest.gaze_learning): the current calibration's clicks as
columns with live errors in px and degrees, its fits and applications, read incrementally."""

from __future__ import annotations

import json
import math
from pathlib import Path

from fastapi.testclient import TestClient
from ingest.gaze_learning import TAILS, report
from ingest.vault import Vault

CALIBRATED = "2026-10-24T15:00:00+00:00"
T0 = 1_792_854_000.0  # 2026-10-24T15:00:00Z


def vector(distance_mm: float) -> list[float]:
    return [1, 0, 0, 0, 1, 0, 0, 0, 1, 0.0, 0.0, distance_mm] + [0.0] * 14


def write(folder: Path, clicks: list[dict], events: list[dict], *, learned: dict | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "gaze.json").write_text(json.dumps({"t": CALIBRATED, "model": "head", "monitor": [2560, 1440],
                                                  "error_px": 85.0, "mm_per_px": 0.233}))  # fmt: skip
    (folder / "clicks.jsonl").write_text("".join(json.dumps(c) + "\n" for c in clicks))
    (folder / "learning.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    if learned is not None:
        (folder / "gaze-learned.json").write_text(json.dumps(learned))


def click(k: int, error: float, base: float, version: int, distance: float = 600.0) -> dict:
    return {"t": T0 + 10 * k, "x": 100, "y": 100, "X": vector(distance), "error_px": error, "base_px": base,
            "v": version, "used": True}  # fmt: skip


def test_the_report_gives_live_errors_by_click_and_the_loss_ratio(tmp_path: Path) -> None:
    folder = tmp_path / "calibration"
    old = dict(click(0, 500.0, 500.0, 0), t=T0 - 3600)  # before this calibration: not shown
    clicks = [old] + [click(k, 100.0, 100.0, 0) for k in range(10)] + [click(10 + k, 60.0, 100.0, 1) for k in range(10)]
    events = [
        {"event": "fit", "t": "2026-10-24T15:01:40+00:00", "version": 1, "accepted": True, "clicks": 10,
         "held_out": 10, "error_px": {"calibration": 100.0, "in_use": 100.0, "learned": 61.0, "fixed_guess": 400.0}},
        {"event": "applied", "t": "2026-10-24T15:01:40.040000+00:00", "version": 1, "after_ms": 40.0},
    ]  # fmt: skip
    write(
        folder, clicks, events, learned={"calibration": CALIBRATED, "version": 1, "t": events[0]["t"], "pose_ref": None}
    )
    out = report(tmp_path)
    assert out["calibrated"] and out["learned"]["version"] == 1
    assert len(out["clicks"]["t"]) == 20 and out["clicks"]["version"] == [0] * 10 + [1] * 10
    live = out["live"]
    assert live["clicks"] == 20 and live["error_px"] == 80.0 and live["calibration_px"] == 100.0
    assert live["ratio"] == 0.8
    # 100 px * 0.233 mm at 600 mm: 2.22 degrees
    assert math.isclose(live["calibration_deg"], math.degrees(math.atan2(23.3, 600.0)), abs_tol=0.01)
    assert [f["version"] for f in out["fits"]] == [1] and out["applied"][0]["after_ms"] == 40.0


def test_new_clicks_are_parsed_without_reading_the_log_again(tmp_path: Path) -> None:
    folder = tmp_path / "calibration"
    write(folder, [click(k, 90.0, 90.0, 0) for k in range(5)], [])
    assert len(report(tmp_path)["clicks"]["t"]) == 5
    tail = TAILS._tails[folder / "clicks.jsonl"]
    offset = tail.offset
    with (folder / "clicks.jsonl").open("a") as out:
        out.write(json.dumps(click(5, 80.0, 90.0, 0)) + "\n")
        out.write(json.dumps(click(6, 80.0, 90.0, 0))[:40])  # an append in flight
    assert len(report(tmp_path)["clicks"]["t"]) == 6 and tail.offset > offset
    with (folder / "clicks.jsonl").open("a") as out:
        out.write(json.dumps(click(6, 80.0, 90.0, 0))[40:] + "\n")
    assert len(report(tmp_path)["clicks"]["t"]) == 7
    # The tracker trims the file by rewriting it: read again from the start.
    (folder / "clicks.jsonl").write_text(json.dumps(click(9, 70.0, 90.0, 0)) + "\n")
    assert report(tmp_path)["clicks"]["error_px"] == [70.0]


def test_no_calibration_reports_so(tmp_path: Path) -> None:
    assert report(tmp_path) == {"calibrated": False}


def test_the_endpoint_serves_it_and_answers_304_until_a_click_lands(client: TestClient, vault: Vault) -> None:
    folder = vault.root / "calibration"
    write(folder, [click(k, 50.0, 70.0, 1) for k in range(3)], [])
    first = client.get("/gaze/learning")
    assert first.json()["calibrated"] and first.json()["live"]["ratio"] == round(50 / 70, 3)
    etag = first.headers["etag"]
    assert client.get("/gaze/learning", headers={"If-None-Match": etag}).status_code == 304
    with (folder / "clicks.jsonl").open("a") as out:
        out.write(json.dumps(click(3, 40.0, 70.0, 1)) + "\n")
    again = client.get("/gaze/learning", headers={"If-None-Match": etag})
    assert again.status_code == 200 and len(again.json()["clicks"]["t"]) == 4

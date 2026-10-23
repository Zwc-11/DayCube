"""A day of capture through the receiver, at the live rates, in a few minutes: does anything grow
without bound, get lost, or slow down?

Drives the real receiver app (``capture.api``) through FastAPI's test client with what the
extension and the tracker send while someone browses: a gaze batch every second (about three
fixations, the tracker's state), a browser batch every second (a heartbeat, plus pages, titles,
focus changes, clicks with positions, selections and media at typical rates), a text snapshot
every 10 s with a dozen blocks. The simulated clock covers ``--hours`` ending now (the receiver
refuses records older than a day). Gaze is the 69 px webcam of eval/gaze_sim.py placed on random
blocks; this measures plumbing, not accuracy.

    uv run python eval/capture_soak.py --hours 8 --output eval/results/capture-soak.json

Checks: every accepted record is in the logs exactly once, no line is malformed, the receiver's
in-memory structures stay under their caps, and request time does not grow with the day. Also
times the tracker's fixation detector per frame, the one per-frame cost this work changed.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services/capture-win"), str(ROOT / "services/gaze")]

GEOMETRY = {
    "vx": 0.0, "vy": 87.0, "scale": 1.0, "inner_w": 2048.0, "inner_h": 1017.0, "screen_w": 2048.0,
    "screen_h": 1152.0, "primary": True, "exact": True, "scroll_y": 0.0, "scroll_h": 6000.0,
}  # fmt: skip
STATE = {
    "tracking": True, "calibrated": True, "error_px": 69.1, "monitor": [2560, 1440], "fps": 29.4,
    "target_fps": 30.0, "face": 1.0, "coverage": 0.9, "noise_px": 24.0,
}  # fmt: skip


def soak(hours: float, seed: int) -> dict:
    from capture.api import SEEN_IDS, create_capture_app
    from capture.attention import LAYOUT_CACHE, TEXT_CACHE
    from fastapi.testclient import TestClient
    from ingest.config import Settings
    from ingest.vault import Vault

    rng = random.Random(seed)
    folder = Path(tempfile.mkdtemp(prefix="daycube-soak-"))
    vault = Vault(folder / "vault")
    vault.initialize()
    app = create_capture_app(Settings(vault_root=vault.root, _env_file=None), receive=True)
    receiver = app.state.receiver
    auth = {"Authorization": f"Bearer {vault.token}"}
    end = datetime.now(UTC) - timedelta(seconds=5)
    start = end - timedelta(hours=hours)
    seconds = int(hours * 3600)
    sent = {"browser": 0, "blocks": 0, "gaze": 0}
    timings: dict[str, list[float]] = {"events": [], "gaze": []}
    doc, blocks, tab, page = "doc-0", [], 7, 0
    with TestClient(app) as client:
        for s in range(seconds):
            now = start + timedelta(seconds=s)
            base = {"tab": tab, "window": 1, "url": f"https://example.test/page/{page}", "title": f"Page {page}"}
            records = []
            if s % 90 == 0:  # a new page every minute and a half
                page += 1
                doc = f"doc-{page:05d}"
                base |= {"url": f"https://example.test/page/{page}", "title": f"Page {page}"}
                records += [
                    {"kind": "page", "transition": "link", "qualifiers": []},
                    {"kind": "title"},
                    {"kind": "focus", "tab": tab, "window": 1, "url": base["url"], "title": base["title"]},
                ]
                blocks = [
                    {"id": f"b{i}", "tag": "p", "rect": [300, 60 + 80 * i, 900, 60]}
                    for i in range(12)
                ]  # fmt: skip
                first = [
                    b | {"text": f"Paragraph {i} of page {page} " * 6, "words": 30, "lines": [
                        {"rect": [300, 60 + 80 * i + 30 * k, 900 if k == 0 else 400, 20],
                         "text": f"Paragraph {i} of page {page} " * (3 if k == 0 else 2)} for k in range(2)
                    ]}
                    for i, b in enumerate(blocks)
                ]  # fmt: skip
                records.append({"kind": "blocks", "doc": doc, "geometry": GEOMETRY, "blocks": first})
            elif s % 10 == 0:
                records.append(
                    {
                        "kind": "blocks",
                        "doc": doc,
                        "geometry": GEOMETRY | {"scroll_y": float(s % 400)},
                        "blocks": blocks,
                    }
                )
            if s % 5 == 0:
                records.append({"kind": "view", "doc": doc, "geometry": GEOMETRY})
            if rng.random() < 1 / 40:
                x, y = rng.uniform(300, 1200), rng.uniform(60, 1000)
                records.append({"kind": "click", "href": None, "text": "Next", "x": x, "y": y, "geometry": GEOMETRY})
            if rng.random() < 1 / 300:
                records.append(
                    {"kind": "selection", "text": "a selected passage of text", "copied": rng.random() < 0.5}
                )
            if rng.random() < 1 / 600:
                records.append({"kind": "media", "state": "playing", "position_s": 0.0, "duration_s": 300.0})
            batch = []
            for r in records:
                item = {"id": uuid.uuid4().hex, "t": now.isoformat()} | r
                if r["kind"] not in ("focus",):
                    item = base | item
                batch.append(item)
            t = time.perf_counter()
            body = client.post("/events", json=batch, headers=auth).json()
            timings["events"].append(time.perf_counter() - t)
            assert not body["rejected"], body["rejected"]
            sent["blocks"] += sum(r["kind"] == "blocks" for r in records)
            sent["browser"] += sum(r["kind"] not in ("blocks", "view") for r in records)
            fixations, f = [], now
            while f < now + timedelta(seconds=1):
                d = rng.uniform(0.2, 0.5)
                bx = rng.choice(blocks)["rect"] if blocks else [300, 60, 900, 60]
                fx = (GEOMETRY["vx"] + bx[0] + rng.uniform(0, bx[2]) + rng.gauss(0, 30)) / GEOMETRY["screen_w"]
                fy = (GEOMETRY["vy"] + bx[1] + rng.uniform(0, bx[3]) + rng.gauss(0, 30)) / GEOMETRY["screen_h"]
                fixations.append({
                    "id": uuid.uuid4().hex, "t": f.isoformat(), "t_end": (f + timedelta(seconds=d)).isoformat(),
                    "x": min(max(fx, 0.0), 1.0), "y": min(max(fy, 0.0), 1.0), "samples": int(d * 30),
                })  # fmt: skip
                f += timedelta(seconds=d + 0.03)
            t = time.perf_counter()
            body = client.post("/gaze", json={"state": STATE, "fixations": fixations}, headers=auth).json()
            timings["gaze"].append(time.perf_counter() - t)
            assert body["accepted"] == len(fixations), body
            sent["gaze"] += len(fixations)

    logs = vault.root / "capture"
    counts, malformed, sizes = {}, 0, {}
    for path in logs.glob("*/*.jsonl"):
        lines = path.read_text(encoding="utf-8").split("\n")[:-1]
        counts[path.name] = counts.get(path.name, 0) + len(lines)
        sizes[path.name] = sizes.get(path.name, 0) + path.stat().st_size
        for line in lines:
            try:
                json.loads(line)
            except ValueError:
                malformed += 1
    a = receiver.attention
    memory = {
        "seen_ids": len(receiver.seen), "seen_ids_cap": SEEN_IDS, "block_texts": len(a.texts),
        "block_texts_cap": TEXT_CACHE, "block_layouts": len(a.layouts), "block_layouts_cap": LAYOUT_CACHE,
        "page_lines": len(a.page.lines) if a.page else 0, "anchors": len(a.anchors.anchors), "anchor_fixations": len(a.anchors.recent),
        "pending_clicks": len(a.anchors.pending), "signals": len(receiver.signals),
    }  # fmt: skip

    def stats(values: list[float]) -> dict:
        ordered = sorted(values)
        n = len(ordered)
        first, last = values[: n // 10], values[-n // 10 :]
        return {
            "requests": n, "median_ms": round(1000 * ordered[n // 2], 2), "p99_ms": round(1000 * ordered[int(n * 0.99)], 2),
            "first_tenth_median_ms": round(1000 * statistics.median(first), 2),
            "last_tenth_median_ms": round(1000 * statistics.median(last), 2),
        }  # fmt: skip

    result = {
        "hours": hours, "sent": sent,
        "logged": {"browser": counts.get("browser.jsonl", 0), "blocks": counts.get("blocks.jsonl", 0),
                   "gaze": counts.get("gaze.jsonl", 0), "attention": counts.get("attention.jsonl", 0),
                   "health": counts.get("health.jsonl", 0), "reading": counts.get("reading.jsonl", 0)},
        "malformed": malformed, "bytes": sizes, "memory": memory,
        "events": stats(timings["events"]), "gaze": stats(timings["gaze"]),
        "accuracy": a.health(end.timestamp()),
    }  # fmt: skip
    result["ok"] = (
        result["logged"]["browser"] == sent["browser"]
        and result["logged"]["blocks"] == sent["blocks"]
        and result["logged"]["gaze"] == sent["gaze"]
        and malformed == 0
        and memory["seen_ids"] <= SEEN_IDS
        and memory["block_texts"] <= TEXT_CACHE
        and memory["block_layouts"] <= LAYOUT_CACHE
        and memory["anchors"] <= 40
        and memory["pending_clicks"] < 10
    )
    return result


def detector_cost(frames: int = 30 * 120) -> dict:
    """Per-frame time of the tracker's smoother and fixation detector on noisy webcam gaze."""
    from gaze.fixations import FixationDetector, Smoother

    rng = random.Random(1)
    smooth, detector = Smoother(), FixationDetector(2560, 1440)
    t0 = time.perf_counter()
    for i in range(frames):
        x, y = smooth(i / 30, 1280 + rng.gauss(0, 60), 720 + rng.gauss(0, 60))
        detector.add(i / 30, x / 2560, y / 1440)
    per_frame = (time.perf_counter() - t0) / frames
    return {"frames": frames, "microseconds_per_frame": round(per_frame * 1e6, 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    result = soak(args.hours, args.seed) | {"tracker": detector_cost()}
    result["wall_s"] = round(time.perf_counter() - started, 1)
    text = json.dumps(result, indent=1)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

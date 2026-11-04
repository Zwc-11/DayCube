"""The input counter's per-event path (capture/inputs.py), replayed from a synthetic raw-input stream.

Every key and every mouse report reaches the counter's window procedure; a gaming mouse reports
1,000-8,000 times a second, so this is the most frequently executed Python in DAYCUBE. The
workload replays RAWINPUT structures, laid out as on 64-bit Windows, through what the window
procedure does per WM_INPUT (``capture.inputs.dispatch`` when present; the earlier inline form,
copy the buffer and parse it, otherwise) into ``Buckets`` with a fake clock. No hooks, devices or
personal data are involved.

    uv run python eval/benchmark_inputs.py --output test-results/inputs-after.json --compare eval/results/inputs-before.json
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import platform
import random
import struct
import subprocess
import sys
from pathlib import Path

from benchmark_notes import measure

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/capture-win"))

from capture import inputs  # noqa: E402

SOURCES = ("services/capture-win/capture/inputs.py", "eval/benchmark_inputs.py", "eval/benchmark_notes.py")
HEADER = 24  # RAWINPUTHEADER on 64-bit Windows: dwType, dwSize, hDevice, wParam


def stream(events: int, seed: int) -> list[bytes]:
    """Raw inputs as GetRawInputData writes them: mostly relative mouse moves, then keys (press and
    release, some auto-repeat), clicks, wheel notches, a few absolute moves and injected input."""
    rng = random.Random(seed)
    out = []
    for _ in range(events):
        r = rng.random()
        device = 0 if r < 0.01 else 0x1234_5678
        if r < 0.80:  # a relative move
            body = struct.pack("<HHHHIii I", 0, 0, 0, 0, 0, rng.randint(-6, 6), rng.randint(-6, 6), 0)
            kind = inputs.RIM_TYPEMOUSE
        elif r < 0.82:  # an absolute move (remote desktop, tablet)
            body = struct.pack("<HHHHIii I", inputs.MOUSE_MOVE_ABSOLUTE, 0, 0, 0, 0, rng.randint(0, 65535),
                               rng.randint(0, 65535), 0)  # fmt: skip
            kind = inputs.RIM_TYPEMOUSE
        elif r < 0.86:  # a click or a wheel notch
            buttons, data = rng.choice([(0x0001, 0), (0x0002, 0), (inputs.WHEEL, 120), (inputs.WHEEL, 0x10000 - 120)])
            body = struct.pack("<HHHHIii I", 0, 0, buttons, data, 0, 0, 0, 0)
            kind = inputs.RIM_TYPEMOUSE
        else:  # a key: press, auto-repeat or release
            vkey = rng.randint(0x30, 0x5A)
            flags = inputs.RI_KEY_BREAK if rng.random() < 0.45 else 0
            body = struct.pack("<HHHHII", 0, flags, 0, vkey, 0x0100, 0)
            kind = inputs.RIM_TYPEKEYBOARD
        header = struct.pack("<IIQQ", kind, HEADER + len(body), device, 0)
        out.append(header + body)
    return out


def workload(raw: list[bytes]) -> callable:
    buffer = ctypes.create_string_buffer(1024)  # the window procedure's reused buffer
    dispatch = getattr(inputs, "dispatch", None)

    def run() -> dict:
        written: list[dict] = []
        clock = [1_800_000_000.0]
        buckets = inputs.Buckets(written.append, private=lambda: False, clock=lambda: clock[0])
        for i, event in enumerate(raw):
            got = len(event)
            ctypes.memmove(buffer, event, got)  # what GetRawInputData does
            if dispatch is not None:
                dispatch(buffer, got, HEADER, buckets)
            else:  # the earlier inline window procedure
                parsed = inputs.parse(buffer.raw[:got], HEADER)
                if parsed is not None:
                    buckets.event(*parsed)
            if i % 50 == 0:
                clock[0] += 0.05  # 1,000 events a second
        buckets.tick()
        clock[0] += inputs.BUCKET_S
        buckets.tick()
        counts = {k: sum(r[k] for r in written) for k in ("keys", "clicks", "wheel", "move_px", "injected")}
        return {
            "records": len(written),
            "counts": counts,
            "sha256": hashlib.sha256(json.dumps(written).encode()).hexdigest(),
        }

    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--events", type=int, default=200_000)
    parser.add_argument("--repeats", type=int, default=11)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    raw = stream(args.events, args.seed)
    run = workload(raw)
    run()  # warmup
    result = measure(run, args.repeats)
    result["per_event_us"] = result["median_ms"] * 1000 / args.events
    report = {
        "workload": {"events": args.events, "seed": args.seed, "repeats": args.repeats, "output": run()},
        "result": result,
        "python": sys.version,
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{args.events} events: median {result['median_ms']:.1f} ms ({result['per_event_us']:.2f} us/event), "
          f"p95 {result['p95_ms']:.1f} ms, peak {result['traced_peak_bytes'] / 1e3:.0f} KB")  # fmt: skip
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        if before["workload"]["output"] != report["workload"]["output"]:
            raise SystemExit("output differs from the comparison run: the counts changed")
        print(f"same output; {before['result']['median_ms'] / result['median_ms']:.2f}x the speed of the comparison")


if __name__ == "__main__":
    main()

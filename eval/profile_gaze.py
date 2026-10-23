"""Read-only gaze rate sampling. Does not open the camera, resume capture or read recordings."""

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seconds < 2:
        parser.error("--seconds must be at least 2")
    opener = build_opener(ProxyHandler({}))
    samples = []
    for index in range(args.seconds):
        with opener.open("http://127.0.0.1:7700/status", timeout=2) as response:
            status = json.loads(response.read())
        gaze = status.get("gaze", {})
        samples.append(
            {
                "paused": status["paused"],
                **{key: gaze.get(key) for key in ("tracking", "reason", "target_fps", "fps", "face")},
            }
        )
        if index + 1 < args.seconds:
            time.sleep(1)
    active = [sample for sample in samples if sample["tracking"] and sample["fps"] is not None]
    root = Path(__file__).resolve().parents[1]
    report = {
        "kind": "read-only-live-gaze-status",
        "sample_interval_s": 1,
        "samples": samples,
        "active_samples": len(active),
        "median_fps": statistics.median(sample["fps"] for sample in active) if active else None,
        "source_sha256": {
            path: hashlib.sha256((root / path).read_bytes()).hexdigest()
            for path in (
                "services/gaze/gaze/track.py",
                "services/gaze/gaze/__main__.py",
                "services/capture-win/capture/api.py",
                "services/capture-win/capture/records.py",
                "eval/profile_gaze.py",
            )
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("active_samples", "median_fps")}))


if __name__ == "__main__":
    main()

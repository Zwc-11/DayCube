"""One photo from a phone scan, two ways (``ingest.recon.single``, docs/glasses.md "One photo"):
lifted to 3D alone (LiDAR depth as the reference, Depth Pro, Depth Anything V2 metric, SHARP) and
rendered where the phone really was 0.25-4 m further on; and located in the scan made of the
same walk's other frames, near its GPS fix, scored against ARKit.

    uv run --project services/recon python eval/single_photo.py [--date 2026-10-24]
        [--moment mo_scan_...] [--photos 8] [--methods flat,lidar,depth-pro,dav2-metric,sharp]
        [--no-locate] [--out vault/logs/single-photo.json]

The renders and located photos go to ``vault/derived/single-photo/<date>/<moment>/`` and stay on
the PC; the report (numbers only) is rewritten after each method. ``check-single-photo.cmd`` runs
it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from glasses_sim import capture_of, day_fixes  # noqa: E402
from ingest.config import Settings  # noqa: E402
from ingest.recon import single  # noqa: E402
from ingest.splat.run import scans  # noqa: E402
from ingest.vault import Vault, read_json  # noqa: E402


def say(line: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {line}", file=sys.stderr, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--date", default="2026-10-24")
    parser.add_argument("--moment", help="A scan moment of the day (default: its first)")
    parser.add_argument("--photos", type=int, default=single.PHOTOS)
    parser.add_argument("--methods", default=",".join(single.METHODS), help=f"Of {', '.join(single.METHODS)}")
    parser.add_argument("--no-locate", action="store_true", help="Skip locating the photos in the scan")
    parser.add_argument("--out", type=Path, help="Report (default vault/logs/single-photo.json)")
    args = parser.parse_args()
    vault = Vault(Settings().vault_root)
    moment_id = args.moment or next(iter(scans(vault, args.date)), None)
    if moment_id is None:
        parser.error(f"no scans on {args.date}")
    moment = vault.day_path(args.date) / "moments" / moment_id
    capture = capture_of(vault, moment)
    if capture is None:
        parser.error(f"{moment_id}: its capture (odometry.csv) is not in the vault")
    scene = read_json(moment / "scene.json") or {}
    video = moment / scene["video"] if (moment / str(scene.get("video"))).is_file() else capture / "rgb.mp4"
    meta = read_json(moment / "meta.json") or {}
    t0 = datetime.fromisoformat(meta["t0"]).timestamp()
    duration = float(meta.get("duration_s") or 600)
    fixes, fixes_from = day_fixes(vault, args.date, t0, t0 + duration)
    out = args.out or vault.root / "logs" / "single-photo.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    work = vault.root / "derived" / "single-photo" / args.date / moment_id
    header = {"date": args.date, "moment": moment_id, "started": datetime.now().astimezone().isoformat(timespec="seconds"),
              "gps_fixes": len(fixes), "gps_from": fixes_from}  # fmt: skip
    say(f"{moment_id}: {video.name}, {len(fixes)} GPS fixes from {fixes_from}; work in {work}")

    def write(report: dict) -> None:
        out.write_text(json.dumps(header | report, indent=1) + "\n", encoding="utf-8")

    report = single.run(capture, video, work, fixes=fixes, t0=t0, photos=args.photos,
                        methods=tuple(m for m in args.methods.split(",") if m), locate_too=not args.no_locate,
                        on_result=write, say=say)  # fmt: skip
    write(report | {"finished": datetime.now().astimezone().isoformat(timespec="seconds")})
    for method, r in report["lift"].items():
        if "error" in r:
            print(f"{method}: {r['error']}")
            continue
        cells = [f"{d} m: {a['psnr']} dB on {a['coverage']} (flat {a['psnr_flat']})" for d, a in r["ahead"].items()]
        print(f"{method}: " + "; ".join(cells) + (f"; depth vs LiDAR {r['depth_vs_lidar']}" if "depth_vs_lidar" in r else ""))
    located = report.get("locate") or {}
    if located:
        print(f"locate: placed {located.get('placed')}/{located.get('photos')}, median {located.get('median_position_m')} m, "
              f"{located.get('median_rotation_deg')} deg, within {located.get('within')}")  # fmt: skip
    print(f"Written to {out}")


if __name__ == "__main__":
    main()

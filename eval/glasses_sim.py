"""A glasses walk simulated from a phone scan (``ingest.recon.simulate``, docs/glasses.md).

The scan's video is cut as glasses would take it (a photo every 3 s, every second, a 5 frames-a-
second stream at 720p's size), the phone's poses are set aside, the images are rebuilt alone with
COLMAP, placed on the map with gravity and the day's GPS, and scored against the poses set aside.
With ``--train`` Brush then trains on each mode twice (the rebuilt poses, and the phone's), scored
on the same held-out frames.

    uv run --project services/capture-win python eval/glasses_sim.py [--date 2026-10-24]
        [--moment mo_scan_...] [--modes photo_3s,photo_1s,stream_5fps] [--train] [--steps 7000]
        [--mapper incremental|global] [--out vault/logs/glasses-sim.json]

The frames and models go to ``vault/derived/glasses-sim/<date>/<moment>/`` and stay on the PC; the
report (numbers only) is rewritten after every mode to ``--out``, so a long run can be read while
it goes. ``check-glasses.cmd`` runs it with ``--train``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from ingest.config import Settings
from ingest.phone import sensor_logger, sources
from ingest.recon import simulate
from ingest.skeleton.parsers.types import Fix
from ingest.splat import brush
from ingest.splat.run import find_capture, scans
from ingest.vault import Vault, read_json


def say(line: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {line}", file=sys.stderr, flush=True)


def capture_of(vault: Vault, moment: Path) -> Path | None:
    own = moment / "capture"
    if (own / "odometry.csv").is_file():
        return own
    return find_capture(vault, moment)


def day_fixes(vault: Vault, date: str, t0: float, t1: float) -> tuple[list[Fix], str]:
    """The phone's GPS from ``t0`` to ``t1`` (epoch seconds), 10 minutes either side: from the kept
    Sensor Logger recordings, else from the day's skeleton (a day imported before recordings were
    kept, as 2026-10-24 was, has its fixes only there)."""
    recordings = sensor_logger.join([sensor_logger.parse(p) for p in sources.sensors_between(vault, t0 - 600, t1 + 600)])
    fixes = sorted((f for r in recordings for f in r.fixes), key=lambda f: f.t)
    if fixes:
        return fixes, "sources/sensors"
    skeleton = read_json(vault.day_path(date) / "skeleton.geojson") or {}
    for feature in skeleton.get("features", []):
        geometry, props = feature.get("geometry") or {}, feature.get("properties") or {}
        if geometry.get("type") != "Point" or not props.get("t"):
            continue
        t = datetime.fromisoformat(props["t"])
        if t0 - 600 <= t.timestamp() <= t1 + 600:
            lon, lat = geometry["coordinates"][:2]
            fixes.append(Fix(t=t, lat=float(lat), lon=float(lon), source=str(props.get("source", "skeleton"))))
    return sorted(fixes, key=lambda f: f.t), "skeleton.geojson"


def trainer(folder: Path, export: Path, *, steps: int, say: object) -> dict:
    """Brush on one training set; the held-out frames of its ``transforms_val.json`` are scored."""
    say(f"Training {folder.parent.name}/{folder.name} ({steps} steps)")  # type: ignore[operator]
    try:
        progress = brush.train(folder, export, steps=steps, log=export.parent / f"{folder.name}.log")
    except RuntimeError as error:
        return {"error": str(error)[-600:]}
    psnr = progress.psnr[-1][1] if progress.psnr else None
    return {"psnr_db": psnr, "psnr_by_step": progress.psnr, "ssim": progress.ssim, "splats": progress.splats,
            "seconds": round(progress.seconds)}  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--date", default="2026-10-24")
    parser.add_argument("--moment", help="A scan moment of the day (default: its first)")
    parser.add_argument("--modes", default=",".join(simulate.MODES), help=f"Of {', '.join(simulate.MODES)}")
    parser.add_argument("--train", action="store_true", help="Train Brush on each mode (the GPU, ~10 min each)")
    parser.add_argument("--steps", type=int, default=7000)
    parser.add_argument("--mapper", choices=["incremental", "global"], default="incremental")
    parser.add_argument("--features", choices=["sift", "aliked"], default="sift",
                        help="aliked: ALIKED + LightGlue (needs services/recon's environment, PyTorch)")
    parser.add_argument("--out", type=Path, help="Report (default vault/logs/glasses-sim.json)")
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
    out = args.out or vault.root / "logs" / ("glasses-sim.json" if args.features == "sift" else f"glasses-sim-{args.features}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = vault.root / "derived" / "glasses-sim" / args.date / moment_id / args.features
    header = {"date": args.date, "moment": moment_id, "started": datetime.now().astimezone().isoformat(timespec="seconds"),
              "gps_fixes": len(fixes), "gps_from": fixes_from, "phone_fit": (read_json(moment / "camera.json") or {}).get("fit")}  # fmt: skip
    say(f"{moment_id}: {video.name}, {len(fixes)} GPS fixes from {fixes_from}; work in {work}")

    def write(report: dict) -> None:
        out.write_text(json.dumps(header | report, indent=1) + "\n", encoding="utf-8")

    report = simulate.simulate(capture, video, work, fixes=fixes, t0=t0, modes=args.modes.split(","), train=args.train,
                               steps=args.steps, mapper=args.mapper, features=args.features, trainer=trainer,
                               on_result=write, say=say)  # fmt: skip
    write(report | {"finished": datetime.now().astimezone().isoformat(timespec="seconds")})
    for name, r in report["modes"].items():
        path = r.get("path", {})
        gps = r.get("gps", {})
        train = r.get("train", {})
        print(f"{name}: placed {r.get('registered', 0)}/{r['images']}, path {path.get('rmse_m')} m RMS, "
              f"GPS {gps.get('rmse_m')} m, scale {gps.get('scale_error_pct')} %, "
              f"PSNR {train.get('recon', {}).get('psnr_db')} dB (phone poses {train.get('phone', {}).get('psnr_db')})")  # fmt: skip
    print(f"checks: {json.dumps(report.get('checks'))}")
    print(f"Written to {out}")


if __name__ == "__main__":
    main()

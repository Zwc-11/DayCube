"""Build fixtures/phone-day: a fictional afternoon exported from a phone, for the viewer's tests.

Everything is synthetic: a home at a made-up address, a 9 km drive north, a walk around a park, the
drive back and upstairs at home (a Sensor Logger zip), a voice memo over the drive back with its
transcript, and a short Stray Scanner capture during the walk (a test pattern for video). Needs
FFmpeg. ``node scripts/start-api-test.mjs`` imports it into the test vault as 2026-10-21.

    uv run python scripts/build-phone-fixture.py
"""

from __future__ import annotations

import csv
import io
import json
import math
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "phone-day"
START = datetime(2026, 10, 21, 18, 0, 0, tzinfo=UTC)  # 14:00 in Toronto
HOME = (43.6600, -79.3900)
PARK = (43.7400, -79.3900)
EARTH_M = 6_371_008.8


def offset(origin: tuple[float, float], east: float, north: float) -> tuple[float, float]:
    lat = origin[0] + math.degrees(north / EARTH_M)
    return lat, origin[1] + math.degrees(east / (EARTH_M * math.cos(math.radians(origin[0]))))


def walk_at(s: float) -> tuple[float, float]:
    a = s * 1.3 / 150.0
    return 150.0 * math.sin(a), -150.0 * math.cos(a) + 150.0


def table(header: list[str], rows: list[list]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def sensor_logger() -> None:
    rows, t = [], 0.0
    for activity, seconds in (("stationary", 240), ("automotive", 600), ("walking", 600), ("automotive", 600),
                              ("stationary", 300)):  # fmt: skip
        for k in range(seconds):
            if activity == "stationary":
                lat, lon = HOME
            elif activity == "automotive":
                a, b = (HOME, PARK) if t < 1000 else (PARK, HOME)
                lat, lon = a[0] + (b[0] - a[0]) * k / seconds, a[1] + (b[1] - a[1]) * k / seconds
            else:
                lat, lon = offset(PARK, *walk_at(k))
            rows.append((t, round(lat, 7), round(lon, 7), activity, 3.3 if t >= 2070 else 0.0))
            t += 1.0
    ns = lambda s: str(int((START.timestamp() + s) * 1e9))  # noqa: E731
    files = {
        "Metadata.csv": table(
            ["version", "device name", "recording epoch time", "recording time", "recording timezone"],
            [[3, "Fixture Phone", int(START.timestamp() * 1000), "2026-10-21_18-00-00", "America/Toronto"]],
        ),  # fmt: skip
        "Location.csv": table(
            [
                "time",
                "seconds_elapsed",
                "altitude",
                "speedAccuracy",
                "bearingAccuracy",
                "latitude",
                "altitudeAboveMeanSeaLevel",
                "bearing",
                "horizontalAccuracy",
                "verticalAccuracy",
                "longitude",
                "speed",
            ],
            [[ns(t), t, 90, -1, -1, lat, 120, -1, 8.0, 10, lon, -1] for t, lat, lon, _, _ in rows[::3]],
        ),  # fmt: skip
        "Barometer.csv": table(
            ["time", "seconds_elapsed", "relativeAltitude", "pressure"],
            [[ns(t), t, alt, 1012.0] for t, *_, alt in rows[::2]],
        ),  # fmt: skip
        "Pedometer.csv": table(
            ["time", "seconds_elapsed", "steps"],
            [[ns(t), t, int(min(max(0, t - 840), 600) * 1.6)] for t, *_ in rows[::20]],
        ),  # fmt: skip
        "Activity.csv": table(
            ["time", "seconds_elapsed", "activity"], [[ns(t), t, activity] for t, _, _, activity, _ in rows[::20]]
        ),  # fmt: skip
    }
    with zipfile.ZipFile(OUT / "12 Example Ave-2026-10-21_18-00-00.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)


def voice_memo() -> None:
    end = START + timedelta(seconds=2070)  # saved 30 s after coming home
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=45",
         "-c:a", "aac", "-b:a", "24k", "-ac", "1", "-metadata",
         f"creation_time={end.strftime('%Y-%m-%dT%H:%M:%S.000000Z')}", str(OUT / "Example Ave 2.m4a")],
        check=True,
    )  # fmt: skip
    segments = [
        (2.0, 4.0, "Almost home now."),
        (9.0, 12.5, "The park was quiet, the bridge was closed."),
        (20.0, 23.0, "Remind me to send the photos tonight."),
    ]
    transcript = {"segments": [
        {"start": a, "end": b, "text": f" {text}", "words": [
            {"start": round(a + (b - a) * i / len(text.split()), 2),
             "end": round(a + (b - a) * (i + 1) / len(text.split()), 2), "word": f" {w}", "p": 0.9}
            for i, w in enumerate(text.split())]}
        for a, b, text in segments
    ]}  # fmt: skip
    (OUT / "Example Ave 2.json").write_text(json.dumps(transcript, indent=1) + "\n")


def scan() -> None:
    capture = OUT / "scan"
    capture.mkdir()
    began = START + timedelta(seconds=840 + 100)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=10",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "35", "-metadata",
         f"creation_time={began.strftime('%Y-%m-%dT%H:%M:%S.000000Z')}", str(capture / "rgb.mp4")],
        check=True,
    )  # fmt: skip
    a = math.radians(25.0)
    lines = ["timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy"]
    e0, n0 = walk_at(100)
    for k in range(300):
        e, n = walk_at(100 + k / 30)
        de, dn = e - e0, n - n0
        x, forward = de * math.cos(a) + dn * math.sin(a), -de * math.sin(a) + dn * math.cos(a)
        lines.append(f"{5000 + k / 30:.4f}, {k:06d}, {x:.4f}, 0.0, {-forward:.4f}, 0, 0, 0, 1, 225.0, 225.0, 160, 120")
    (capture / "odometry.csv").write_text("\n".join(lines) + "\n")
    (capture / "camera_matrix.csv").write_text("225.0, 0.0, 160\n0.0, 225.0, 120\n0.0, 0.0, 1.0\n")


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    sensor_logger()
    voice_memo()
    scan()
    (OUT / "README.md").write_text(
        "# Fictional phone afternoon\n\n"
        "Synthetic phone exports for the viewer's tests: a Sensor Logger zip (made-up address and\n"
        "track), a voice memo of a tone with a written transcript, and a Stray Scanner capture of a test\n"
        "pattern. Rebuild with `uv run python scripts/build-phone-fixture.py`.\n"
    )
    print(sorted(p.relative_to(OUT).as_posix() for p in OUT.rglob("*") if p.is_file()))


if __name__ == "__main__":
    main()

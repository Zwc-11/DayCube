"""Phone recordings kept with the vault (``vault/sources``), so a day is always made from all of them.

- **Sensors** (``sources/sensors/<id>/``): the five CSVs the importer reads from each Sensor Logger
  recording, or each hour from DayCube Capture: Metadata, Location, Barometer, Pedometer and
  Activity. That is a few MB an hour; the high-rate sensors are left out.
  - Each import rebuilds the places, trips and GPS fixes of every day it touches from all of that
    day's kept recordings.
  - Before, importing a second recording of a day replaced what the first had given it.
- **Audio** (``sources/audio/<device>/<UTC start>.<ext>``): the app's hours of sound. The
  ``conversations`` job cuts them into talk moments (``ingest/jobs/stages.py``).

``index.json`` in each folder lists what is kept, with its time span.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from ..vault import Vault, atomic_text, read_json

SENSOR_CSV = ("Metadata.csv", "Location.csv", "Barometer.csv", "Pedometer.csv", "Activity.csv")
# The app's files: sensors-20261025T140000Z (a folder) and audio-20261025T140000Z.m4a, maybe
# with the paired device's id before them (ph-1a2b3c4d-...).
APP_STAMP = re.compile(r"(?:^|[-_])(sensors|audio)-(\d{8}T\d{6}Z)$")


def app_start(path: Path) -> tuple[str, datetime] | None:
    """(kind, UTC start) of a DayCube Capture file named by its start, else None."""
    match = APP_STAMP.search(path.stem if path.is_file() or path.suffix else path.name)
    if not match:
        return None
    return match[1], datetime.strptime(match[2], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:60] or "recording"


def keep_sensors(vault: Vault, path: Path, t0: float, t1: float, name: str) -> Path:
    """Copy a recording's CSVs into ``sources/sensors``; the same recording again replaces itself."""
    root = vault.root / "sources" / "sensors"
    key = datetime.fromtimestamp(t0, UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + _slug(name or path.stem)
    folder = root / key
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    if path.is_file():
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                if Path(member).name in SENSOR_CSV:
                    (folder / Path(member).name).write_bytes(archive.read(member))
    else:
        for csv_name in SENSOR_CSV:
            if (path / csv_name).is_file():
                shutil.copy2(path / csv_name, folder / csv_name)
    (folder / "name.txt").write_text(name, encoding="utf-8")  # the recording's name, which named its first stay
    index = read_json(root / "index.json") if (root / "index.json").is_file() else {}
    index[key] = {"t0": t0, "t1": t1, "name": name, "from": path.name}
    atomic_text(root / "index.json", json.dumps(index, indent=1) + "\n")
    return folder


def sensors_between(vault: Vault, t0: float, t1: float) -> list[Path]:
    """Kept recordings that overlap [t0, t1] (epoch seconds)."""
    root = vault.root / "sources" / "sensors"
    index = read_json(root / "index.json") if (root / "index.json").is_file() else {}
    return [
        root / key
        for key, info in sorted(index.items())
        if info["t1"] >= t0 and info["t0"] <= t1 and (root / key).is_dir()
    ]


def keep_audio(vault: Vault, path: Path, device: str, start: datetime, duration_s: float) -> str:
    """Move the app's hour of audio into ``sources/audio``; its path relative to the vault."""
    root = vault.root / "sources" / "audio" / _slug(device)
    root.mkdir(parents=True, exist_ok=True)
    target = root / (start.strftime("%Y%m%dT%H%M%SZ") + path.suffix.lower())
    shutil.copy2(path, target)
    index_path = vault.root / "sources" / "audio" / "index.json"
    index = read_json(index_path) if index_path.is_file() else {}
    relative = target.relative_to(vault.root).as_posix()
    index[relative] = {"t0": start.timestamp(), "t1": start.timestamp() + duration_s, "device": device}
    atomic_text(index_path, json.dumps(index, indent=1) + "\n")
    return relative


def backfill(vault: Vault) -> int:
    """Once per vault: keep the sensor recordings imported before recordings were kept (still in
    ``inbox/phone/imported``), so rebuilding their days does not lose them. How many were kept."""
    from . import sensor_logger

    root = vault.root / "sources" / "sensors"
    marker = root / ".backfilled"
    if marker.exists():
        return 0
    kept = 0
    imported = vault.root / "inbox" / "phone" / "imported"
    for batch in sorted(imported.iterdir()) if imported.is_dir() else []:
        for path in sorted(batch.iterdir()) if batch.is_dir() else []:
            try:
                if not sensor_logger.is_recording(path):
                    continue
                recording = sensor_logger.parse(path)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                continue
            if recording.fixes or recording.altitude:
                keep_sensors(vault, path, recording.t0, recording.t1, recording.name)
                kept += 1
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{kept} recordings from inbox/phone/imported\n", encoding="utf-8")
    return kept

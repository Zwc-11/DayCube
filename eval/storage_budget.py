"""Bytes per day for every vault stream at the capture rates in the technical spec.

Generates one synthetic 16-hour day in memory (no vault, no network), encodes each stream in the
current on-disk format and in candidate compact formats, and reports sizes. Streams the repo does
not produce yet (fixes at 1 Hz, OCR every 30 s, gaze fixations) use the spec's rates and field sets.
Media sizes cannot be synthesized honestly; they are reported from stated per-item assumptions.

    uv run --with zstandard --with pyarrow python eval/storage_budget.py --output test-results/storage.json
"""

from __future__ import annotations

import argparse
import io
import json
import math
import random
import sqlite3
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import zstandard

AWAKE_S = 16 * 3600
SCREEN_S = 8 * 3600
SHOT_INTERVAL_S = 30  # spec 4.1: screenshot every 30 s while the screen is in use
WINDOW_INTERVAL_S = 5  # spec 4.1: foreground window sample every 5 s
FIXATIONS_PER_S = 3.5  # typical reading rate; spec 4.1 logs fixations only
EMBEDDING_DIM = 384  # spec 6.1: bge-small
START = datetime(2026, 10, 20, 7, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Stream:
    name: str
    records: list[dict]
    columns: dict[str, str]  # column -> arrow type name for the columnar encodings


def iso(seconds: float) -> str:
    return (START + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def words(rng: random.Random, vocabulary: list[str], count: int) -> str:
    return " ".join(rng.choices(vocabulary, weights=ZIPF[: len(vocabulary)], k=count))


ZIPF = [1 / (rank + 1) for rank in range(6000)]


def synthesize(seed: int = 20) -> tuple[list[Stream], dict[str, int]]:
    rng = random.Random(seed)
    vocabulary = ["".join(rng.choices("etaoinshrdlucmfwypvbgk", k=rng.randint(2, 9))) for _ in range(6000)]

    # Fixes at 1 Hz: visits with GPS noise, walks between them at 1.4 m/s.
    fixes, lat, lon, place, t = [], 43.66, -79.39, 0, 0
    while t < AWAKE_S:
        stay = rng.randint(1200, 7200)
        for _ in range(stay):
            fixes.append(
                {
                    "t": iso(t),
                    "lat": round(lat + rng.gauss(0, 4e-5), 7),
                    "lon": round(lon + rng.gauss(0, 6e-5), 7),
                    "alt_rel": round(rng.gauss(0, 0.3), 2),
                    "floor": 0,
                    "floor_conf": round(rng.uniform(0.6, 0.98), 2),
                    "place": f"place{place}",
                    "x": round(rng.uniform(-8, 8), 2),
                    "y": round(rng.uniform(-6, 6), 2),
                    "pos_conf": 0.4,
                    "fidelity": "estimated",
                    "source": "recorder",
                }
            )
            t += 1
        heading = rng.uniform(0, 6.283)
        for _ in range(rng.randint(300, 1200)):
            lat += 1.4 * 9e-6 * rng.gauss(1, 0.1) * math.cos(heading)
            lon += 1.4 * 1.24e-5 * rng.gauss(1, 0.1) * math.sin(heading)
            fixes.append(
                {
                    "t": iso(t),
                    "lat": round(lat, 7),
                    "lon": round(lon, 7),
                    "alt_rel": None,
                    "floor": None,
                    "floor_conf": None,
                    "place": None,
                    "x": None,
                    "y": None,
                    "pos_conf": None,
                    "fidelity": "known",
                    "source": "recorder",
                }
            )
            t += 1
        place += 1
    fixes = fixes[:AWAKE_S]

    # Foreground window every 5 s; sessions of 1-15 minutes on one app/title.
    apps = ["code.exe", "chrome.exe", "slack.exe", "explorer.exe", "zoom.exe"]
    windows, t = [], 0
    while t < SCREEN_S:
        app, title, pid = rng.choice(apps), words(rng, vocabulary, rng.randint(3, 8)), rng.randint(1000, 30000)
        for _ in range(rng.randint(12, 180)):
            windows.append({"t": iso(t), "app": app, "title": title, "pid": pid})
            t += WINDOW_INTERVAL_S

    # OCR every 30 s. A page persists for several shots; scrolling replaces a few blocks per shot.
    shots, t, shot = [], 0, 0
    while t < SCREEN_S:
        app, title = rng.choice(apps), words(rng, vocabulary, rng.randint(3, 8))
        blocks = [words(rng, vocabulary, rng.randint(3, 40)) for _ in range(25)]
        for _ in range(rng.randint(2, 20)):
            for _ in range(rng.randint(0, 4)):
                blocks[rng.randrange(len(blocks))] = words(rng, vocabulary, rng.randint(3, 40))
            shots.append(
                {
                    "shot": f"{shot:05d}",
                    "t": iso(t),
                    "app": app,
                    "title": title,
                    "w": 1920,
                    "h": 1080,
                    "blocks": [
                        {
                            "id": f"b{i}",
                            "bbox": [rng.randint(0, 1800), 40 * i, rng.randint(200, 1920), 40 * i + 32],
                            "text": text,
                            "conf": round(rng.uniform(0.8, 0.99), 2),
                            "attention": round(rng.random(), 2),
                            "dwell_ms": rng.randint(0, 6000),
                        }
                        for i, text in enumerate(blocks)
                    ],
                }
            )
            shot += 1
            t += SHOT_INTERVAL_S

    gaze = [
        {
            "t": iso(i / FIXATIONS_PER_S),
            "x": rng.randint(0, 1919),
            "y": rng.randint(0, 1079),
            "dur_ms": rng.randint(100, 600),
            "conf": round(rng.uniform(0.5, 1.0), 2),
            "fix_id": i,
        }
        for i in range(int(SCREEN_S * FIXATIONS_PER_S))
    ]

    events = [
        {
            "id": f"ev_{i:06d}",
            "t": iso(i * AWAKE_S / 1500),
            "type": rng.choice(["screen.app", "screen.page", "screen.read", "said.quote"]),
            "label": words(rng, vocabulary, 6),
            "place": f"place{rng.randrange(8)}",
            "floor": 0,
            "x": 1.0,
            "y": 2.0,
            "source": {"kind": "browser_ext", "ref": f"screens/{i:05d}.jpg"},
            "confidence": 0.9,
            "fidelity": "known",
            "text": words(rng, vocabulary, 30),
        }
        for i in range(1500)
    ]

    streams = [
        Stream(
            "fixes",
            fixes,
            {
                "t": "timestamp",
                "lat": "float64",
                "lon": "float64",
                "alt_rel": "float32",
                "floor": "int8",
                "floor_conf": "float32",
                "place": "dict",
                "x": "float32",
                "y": "float32",
                "pos_conf": "float32",
                "fidelity": "dict",
                "source": "dict",
            },
        ),
        Stream("windows", windows, {"t": "timestamp", "app": "dict", "title": "dict", "pid": "int32"}),
        Stream(
            "gaze",
            gaze,
            {"t": "timestamp", "x": "int16", "y": "int16", "dur_ms": "int16", "conf": "float32", "fix_id": "int32"},
        ),
        Stream("events", events, {}),
        Stream("ocr", shots, {}),
    ]
    return streams, {"shots": len(shots), "blocks": sum(len(s["blocks"]) for s in shots)}


def jsonl(records: Iterable[dict]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode()


def geojson_as_written(fixes: list[dict]) -> bytes:
    """skeleton.geojson exactly as Vault.write_json writes it today (indent=2)."""
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]},
            "properties": {k: v for k, v in f.items() if k not in ("lat", "lon")},
        }
        for f in fixes
    ]
    return (json.dumps({"type": "FeatureCollection", "features": features}, indent=2) + "\n").encode()


def zstd(level: int) -> Callable[[bytes], bytes]:
    return zstandard.ZstdCompressor(level=level).compress


def parquet(stream: Stream) -> bytes | None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return None
    types = {
        "timestamp": pa.timestamp("ms", tz="UTC"),
        "float64": pa.float64(),
        "float32": pa.float32(),
        "int8": pa.int8(),
        "int16": pa.int16(),
        "int32": pa.int32(),
        "dict": pa.dictionary(pa.int32(), pa.string()),
    }
    arrays = {}
    for column, kind in stream.columns.items():
        values = [r[column] for r in stream.records]
        if kind == "timestamp":
            values = [datetime.fromisoformat(v) for v in values]
        arrays[column] = pa.array(values, type=types[kind])
    sink = io.BytesIO()
    float_columns = [c for c, k in stream.columns.items() if k in ("float64", "float32")]
    pq.write_table(
        pa.table(arrays),
        sink,
        compression="zstd",
        compression_level=9,
        use_byte_stream_split=float_columns,
        column_encoding={c: "DELTA_BINARY_PACKED" for c, k in stream.columns.items() if k in ("int16", "int32")},
        use_dictionary=[c for c, k in stream.columns.items() if k == "dict"],
    )
    return sink.getvalue()


def parse_times(geojson: bytes, columnar: bytes | None) -> dict[str, float]:
    """Median of five: parsing the day's fixes from each encoding into Python values."""

    def median_ms(operation: Callable[[], Any]) -> float:
        samples = []
        for _ in range(5):
            started = time.perf_counter()
            operation()
            samples.append((time.perf_counter() - started) * 1000)
        return round(sorted(samples)[2], 1)

    times = {"geojson": median_ms(lambda: json.loads(geojson))}
    if columnar is not None:
        import pyarrow.parquet as pq

        times["parquet_columns"] = median_ms(lambda: pq.read_table(io.BytesIO(columnar)))
        times["parquet_rows"] = median_ms(lambda: pq.read_table(io.BytesIO(columnar)).to_pylist())
    return times


def dedupe_ocr(shots: list[dict]) -> tuple[list[dict], int]:
    """Collapse a block that repeats on consecutive shots of the same window into one run."""
    runs: list[dict] = []
    open_runs: dict[tuple[str, str], dict] = {}
    for shot in shots:
        seen = {}
        for block in shot["blocks"]:
            key = (shot["title"], block["text"])
            run = open_runs.get(key)
            if run is None:
                run = {"t": shot["t"], "t_end": shot["t"], "title": shot["title"], "text": block["text"], "n": 0}
                runs.append(run)
            run["t_end"], run["n"] = shot["t"], run["n"] + 1
            seen[key] = run
        open_runs = seen
    return runs, len(runs)


def fts_bytes(texts: list[str]) -> int:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "index.sqlite"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE units (id INTEGER PRIMARY KEY, text TEXT NOT NULL)")
        db.execute("CREATE VIRTUAL TABLE units_fts USING fts5(text, content='units', content_rowid='id')")
        db.executemany("INSERT INTO units(text) VALUES (?)", ((t,) for t in texts))
        db.execute("INSERT INTO units_fts(units_fts) VALUES ('rebuild')")
        db.execute("INSERT INTO units_fts(units_fts) VALUES ('optimize')")
        db.commit()
        db.execute("VACUUM")
        db.close()
        return path.stat().st_size


def measure() -> dict[str, Any]:
    streams, counts = synthesize()
    by_name = {s.name: s for s in streams}
    report: dict[str, Any] = {"assumptions": {}, "streams": {}, "counts": counts}

    for stream in streams:
        raw = geojson_as_written(stream.records) if stream.name == "fixes" else jsonl(stream.records)
        row = {"records": len(stream.records), "current": len(raw)}
        compact = jsonl(stream.records)
        row["jsonl"] = len(compact)
        row["jsonl_zstd10"] = len(zstd(10)(compact))  # the level Vault.compact_day uses
        row["jsonl_zstd19"] = len(zstd(19)(compact))
        columnar = parquet(stream) if stream.columns else None
        if columnar is not None:
            row["parquet_zstd"] = len(columnar)
        if stream.name == "fixes":
            report["fixes_parse_ms"] = parse_times(raw, columnar)
        report["streams"][stream.name] = row

    runs, run_count = dedupe_ocr(by_name["ocr"].records)
    block_texts = [f"{s['title']}: {b['text']}" for s in by_name["ocr"].records for b in s["blocks"]]
    event_texts = [f"{e['label']} {e['text']}" for e in by_name["events"].records]
    run_texts = [f"{r['title']}: {r['text']}" for r in runs]
    report["index"] = {
        "units_per_block": len(block_texts) + len(event_texts),
        "units_deduplicated": run_count + len(event_texts),
        "fts_per_block": fts_bytes(block_texts + event_texts),
        "fts_deduplicated": fts_bytes(run_texts + event_texts),
    }
    units = report["index"]
    report["embeddings"] = {
        kind: {"per_block": units["units_per_block"] * size, "deduplicated": units["units_deduplicated"] * size}
        for kind, size in (("float32", EMBEDDING_DIM * 4), ("int8", EMBEDDING_DIM), ("binary", EMBEDDING_DIM // 8))
    }

    # Media: stated assumptions, not measurements.
    thumbnail_kb, static_share = 28, 0.6
    report["assumptions"] = {
        "thumbnail_kb": thumbnail_kb,
        "static_share_of_shots": static_share,
        "moment_video_mb_per_min_1080p_hevc": 60,
        "keyframe_kb": 90,
        "depth_png_kb": 450,
        "splat_ply_mb": 60,
        "splat_spz_mb": 6,
    }
    shots = counts["shots"]
    report["media"] = {
        "thumbnails": shots * thumbnail_kb * 1024,
        "thumbnails_deduplicated": int(shots * (1 - static_share)) * thumbnail_kb * 1024,
        "moment_per_minute_source": 60 * 2**20,
        "moment_per_minute_keyframes": 120 * 90 * 1024,
        "moment_per_minute_depth_all_keyframes": 120 * 450 * 1024,
        "moment_per_minute_depth_scene_keyframe": 450 * 1024,
    }
    return report


def mb(value: int) -> str:
    return f"{value / 2**20:,.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = measure()
    for name, row in report["streams"].items():
        cells = "  ".join(f"{k}={mb(v)}" for k, v in row.items() if k != "records")
        print(f"{name:8} n={row['records']:>7,}  {cells}  (MB)")
    print("parse    fixes " + "  ".join(f"{k}={v} ms" for k, v in report["fixes_parse_ms"].items()))
    index = report["index"]
    print(
        f"index    units {index['units_per_block']:,} -> {index['units_deduplicated']:,}  "
        f"fts {mb(index['fts_per_block'])} -> {mb(index['fts_deduplicated'])} MB"
    )
    for kind, row in report["embeddings"].items():
        print(f"vectors  {kind:8} {mb(row['per_block'])} -> {mb(row['deduplicated'])} MB")
    for name, value in report["media"].items():
        print(f"media    {name:40} {mb(value)} MB")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

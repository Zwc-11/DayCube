"""A recording into an items folder: ``python -m ingest.items.run scan <capture> --out <folder>`` or
``... video <file> --out <folder>``.

A scan's items share one frame (ARKit's metres), so the same thing seen from many frames joins into
one item and its ``spread_m`` says how well the frames agree. A plain video has no camera path yet:
each shot's things are placed in that shot's own camera, by a model's depth, and joined only within
the shot.

``report`` is written beside the items (``report.json``): counts, seconds per stage, and for a scan
how often an item's place, seen again from each frame that observed it, falls inside the box found
in that frame (the check that the 3D places are right, which needs no labels).
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import frames as sources
from . import models, store
from .lift import Frame, place
from .merge import Item, Observation, merge

MIN_SEEN_WORLD = 2  # in a scan an item must be seen from two frames; one look is a guess


def observe(frame: Frame, caption: bool = True) -> tuple[list[Observation], dict]:
    """What is in one frame: placed observations (without embeddings yet) and the frame's index
    entry (caption, text read in it)."""
    seen = models.look(sources.upright(frame.image, frame.turn), caption)
    H, W = frame.image.shape[:2]
    found = []
    for d in seen.things:
        box = sources.box_back(d.box, frame.turn, W, H)
        where = place(frame, box)
        if where is None:
            continue
        found.append(Observation(frame.index, frame.t, box, d.label, d.score, where.position, where.size_m,
                                 where.distance_m, where.depth, np.zeros(1)))  # fmt: skip
    entry = {"index": frame.index, "t": round(frame.t, 2)}
    if caption:
        entry |= {"caption": seen.caption, "text": seen.text}
    return found, entry


def embed(frame: Frame, observations: list[Observation]) -> None:
    if not observations:
        return
    crops = [sources.upright(models.crop(frame.image, o.box), frame.turn) for o in observations]
    for o, v in zip(observations, models.embed_images(crops), strict=True):
        o.vector = v


def reprojection(items: list[Item], frames: dict[int, Frame]) -> dict:
    """For items seen more than once: the share of their observations whose box holds the item's
    place when it is projected back into that frame, and the median distance (in box widths) from
    the box's centre."""
    inside, offsets = [], []
    w2cs = {index: np.linalg.inv(f.c2w) for index, f in frames.items()}
    for item in items:
        if len(item.observations) < 2:
            continue
        p = item.position
        for o in item.observations:
            f, w2c = frames[o.frame], w2cs[o.frame]
            cam = w2c[:3, :3] @ p + w2c[:3, 3]
            if -cam[2] <= 0.05:
                inside.append(False)
                continue
            fx, fy, cx, cy = f.K
            u, v = cam[0] / -cam[2] * fx + cx, -cam[1] / -cam[2] * fy + cy
            x0, y0, x1, y1 = o.box
            inside.append(bool(x0 <= u <= x1 and y0 <= v <= y1))
            offsets.append(
                float(np.hypot((u - (x0 + x1) / 2) / max(x1 - x0, 1), (v - (y0 + y1) / 2) / max(y1 - y0, 1)))
            )
    return {"observations": len(inside), "inside_box": round(float(np.mean(inside)), 3) if inside else None,
            "median_offset_box_widths": round(float(np.median(offsets)), 3) if offsets else None}  # fmt: skip


def best_crop(item: Item, frames: dict[int, Frame]) -> np.ndarray:
    o = item.best()
    f = frames[o.frame]
    return sources.upright(models.crop(f.image, o.box, margin=0.15), f.turn)


def observe_frames(frames: dict[int, Frame], captions: bool = True,
                   say: Callable[[str], None] = print) -> tuple[list[Observation], list[dict]]:  # fmt: skip
    """Every frame looked at: the observations (with their embeddings) and the frames' index entries."""
    observations: list[Observation] = []
    entries = []
    for n, f in enumerate(frames.values()):
        found, entry = observe(f, captions)
        embed(f, found)
        observations += found
        entries.append(entry)
        say(
            f"frame {n + 1}/{len(frames)} ({f.index}): {len(found)} things: {', '.join(sorted({o.label for o in found}))[:90]}"
        )
    return observations, entries


def index_frames(frames: dict[int, Frame], out: Path, source: dict, depth: str = "lidar", captions: bool = True,
                 say: Callable[[str], None] = print, seconds: dict[str, float] | None = None,
                 seen: tuple[list[Observation], list[dict]] | None = None) -> dict:  # fmt: skip
    """Frames that share one world (each with its pose and depth) into an items folder. ``seen``:
    what ``observe_frames`` already found in them, to make the items again without the models."""
    timing: dict[str, float] = dict(seconds or {})
    t0 = time.time()
    observations, entries = seen or observe_frames(frames, captions, say)
    timing["detect_embed_s"] = time.time() - t0
    t0 = time.time()
    merged = merge(observations) if observations else []
    items = [i for i in merged if len(i.observations) >= MIN_SEEN_WORLD]
    timing["merge_s"] = time.time() - t0
    by_frame: dict[int, list[str]] = {}
    for k, item in enumerate(items):
        for o in item.observations:
            by_frame.setdefault(o.frame, []).append(f"i{k + 1:04d}")
    for entry in entries:
        entry["items"] = sorted(set(by_frame.get(entry["index"], [])))
    record = store.write(out, items, [best_crop(i, frames) for i in items], source={**source, "frames": len(frames)},
                         frame="world", models={"detector": models.DETECTOR, "embedder": models.EMBEDDER, "depth": depth},
                         frames=entries)  # fmt: skip
    spreads = [i.spread_m for i in items]
    names: dict[str, int] = {}
    for i in items:
        names[i.label] = names.get(i.label, 0) + 1
    report = {
        "frames": len(frames), "observations": len(observations), "things_before_merging": len(merged),
        "items": len(items), "seen_once_dropped": len(merged) - len(items),
        "median_times_seen": float(np.median([len(i.observations) for i in items])) if items else 0,
        "median_spread_m": round(float(np.median(spreads)), 3) if spreads else None,
        "p90_spread_m": round(float(np.percentile(spreads, 90)), 3) if spreads else None,
        "reprojection": reprojection(items, frames),
        "depth": {k: sum(i.best().depth == k for i in items) for k in ("lidar", "estimate", "model")},
        "names": dict(sorted(names.items(), key=lambda kv: -kv[1])),
        "seconds": {k: round(v, 1) for k, v in timing.items()},
        "seconds_per_frame": round(timing["detect_embed_s"] / max(len(frames), 1), 2),
    }  # fmt: skip
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    say(json.dumps(report))
    return record


def index_scan(capture: Path, out: Path, every_s: float = sources.KEY_EVERY_S, limit: int | None = None,
               captions: bool = True, say: Callable[[str], None] = print) -> dict:  # fmt: skip
    t0 = time.time()
    frames = {f.index: f for f in sources.scan_frames(capture, every_s, limit=limit)}
    took = time.time() - t0
    say(f"{len(frames)} frames decoded in {took:.0f} s")
    source = {
        "kind": "scan",
        "file": capture.name,
        "every_s": every_s,
        "recorded": sources.recorded(capture / "rgb.mp4"),
    }
    return index_frames(frames, out, source, "lidar", captions, say, {"frames_s": took})


def index_video(video: Path, out: Path, captions: bool = True, say: Callable[[str], None] = print) -> dict:
    timing = {"start": time.time()}
    items: list[Item] = []
    shots: list[int | None] = []
    crops: list[np.ndarray] = []
    entries = []
    for n, (a, b), f in sources.video_frames(video, models.model_depth):
        found, entry = observe(f, captions)
        embed(f, found)
        merged = merge(found) if found else []
        entry.update(
            {
                "shot": n,
                "t0": round(a, 2),
                "t1": round(b, 2),
                "items": [f"i{len(items) + k + 1:04d}" for k in range(len(merged))],
            }
        )
        entries.append(entry)
        items += merged
        shots += [n] * len(merged)
        crops += [sources.upright(models.crop(f.image, i.best().box, margin=0.15), f.turn) for i in merged]
        say(f"shot {n} ({a:.1f}-{b:.1f} s): {len(merged)} things; {entry.get('caption', '')[:70]}")
    source = {"kind": "video", "file": video.name, "shots": len(entries), "recorded": sources.recorded(video)}
    record = store.write(out, items, crops, source=source, frame="shot",
                         models={"detector": models.DETECTOR, "embedder": models.EMBEDDER, "depth": "Depth Anything V2 Metric Small"},
                         frames=entries, shots=shots)  # fmt: skip
    report = {"shots": len(entries), "items": len(items), "seconds": round(time.time() - timing["start"], 1)}
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    say(json.dumps(report))
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=["scan", "video", "search"])
    parser.add_argument("source", help="A Stray Scanner capture folder, a video file, or the words to search for")
    parser.add_argument("--out", type=Path, required=True, help="The items folder to write (or to search)")
    parser.add_argument("--every", type=float, default=sources.KEY_EVERY_S, help="Seconds between a scan's frames")
    parser.add_argument("--limit", type=int, help="At most this many frames of a scan")
    parser.add_argument("--no-captions", action="store_true")
    args = parser.parse_args()
    if args.kind == "scan":
        index_scan(Path(args.source), args.out, args.every, args.limit, not args.no_captions)
    elif args.kind == "video":
        index_video(Path(args.source), args.out, not args.no_captions)
    else:
        from .search import search

        for hit in search(args.source, [args.out], models.embed_texts):
            print(f"{hit['score']:.3f}  {hit['id']}  {hit['label']:<24} seen {hit['seen']}x  at {hit['position']}  "
                  f"{hit['first_s']}-{hit['last_s']} s")  # fmt: skip


if __name__ == "__main__":
    main()

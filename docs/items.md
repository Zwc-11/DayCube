# Items: the things in a recording, placed in 3D and found by words

`services/ingest/ingest/items/` turns a LiDAR scan or a plain video into items: each with a place,
a size, when it was seen, its best picture and a vector that words can be compared with. Nothing is
trained; three open models are used as released.

```
recording ─► frames (pose + depth) ─► detect (boxes, names, caption, text) ─► place (box → 3D)
          ─► embed (a vector per picture) ─► merge (same thing, one item) ─► store ─► find
```

## When

For a scan in the vault with its depth, the receiver queues the `scan_items` [job](jobs.md):
`ingest.items.run` in the 3D environment (`services/recon`, or the desktop app's 3D pack), on the
GPU when the PC is free, written to `vault/<date>/items/<a set id made from the scan's>`. Without
that environment the job waits and `capture doctor` says how to install it. A plain video is
run by hand, below.

## Run it

```
python -m ingest.items.run scan  <Stray Scanner capture folder> --out vault/<date>/items/it_<12 hex>
python -m ingest.items.run video <file.mp4>                     --out vault/<date>/items/it_<12 hex>
python -m ingest.items.run search "a wooden bench"              --out vault/<date>/items/it_<12 hex>
```

It needs PyTorch and `transformers` (the recon environment); weights download from the Hugging
Face hub on first use. A GPU is not required: on two CPU cores a frame's three readings (its things, a caption, the
writing in it) cost 32 to 46 CPU-seconds. The picture goes through Florence-2's vision tower once
for all three, which took 39% off (53 to 72 CPU-seconds before, the same answers).

| Role | Model | Licence |
| --- | --- | --- |
| Boxes and names, captions, text in the picture | Florence-2 base | MIT |
| Picture and text vectors | SigLIP 2 base/16-224 | Apache-2.0 |
| Depth where there is no LiDAR | Depth Anything V2 Metric Small (indoor, outdoor) | Apache-2.0 |

## What is stored

`vault/<date>/items/<id>/items.json` (format in `items/store.py`), `vectors.f16` (one unit vector
per item) and `crops/` (each item's best look). `report.json` holds the run's counts, seconds per
stage, and the reprojection check. A set says when its recording was made (the video's creation
time and length) and, when a job made it, which scan it came from:
[deleting a span](capture.md#deleting-a-span) removes the sets of what it deletes.

- **A scan** shares one frame for the whole recording (ARKit's metres, y up): the same thing seen
  from several frames is one item, kept only when seen at least twice, and `spread_m` is how far its
  sightings lie from its place.
  - Two sightings are the same thing when they are near each other (against their own size) and
    look alike (the cosine of their picture vectors).
  - Two boxes in one frame are never the same thing, however near and alike. Without this rule a
    row of chairs or bottles became one item placed between them.
- **A plain video** has no camera path yet: each shot's things are placed in that shot's own camera
  by a depth model (`"depth": "model"`), and joined only within the shot.

Depth is labelled by how it was known: `lidar` (measured), `estimate` (ARKit's guess beyond the
LiDAR's 5.5 m) or `model`.

## API

`GET /items`, `GET /items/find?q=<words>` (by name, over the whole vault), `GET /items/{date}/{id}`,
`GET /items/{date}/{id}/crops/{name}`. Finding by what a thing looks like needs the embedder and is
in `items/search.py`; the app's core runtime has no PyTorch, so the API finds by name only.

## In the viewer

`?view=items` (the box in the graph page's bar): the things of a recording as a list, most seen
first, with each one's best picture, and as balls in 3D where each was, as wide as the thing, on
a metre grid. Picking either shows the item's place, size, how well its sightings agree and how
its depth was known. The box above the list finds by any name the thing was given. A plain
video's items are shown one shot at a time, since each shot has its own camera
(`apps/viewer/src/components/items/`, `apps/viewer/e2e/items.spec.ts`).

## Checks

`services/ingest/tests/test_items.py` (the models stood in: geometry, merging, turning of sideways
frames, storage, the API). `eval/items_tum.py` runs the real models on the TUM RGB-D office
recording, whose camera path and depth are measured, and reports how often an item's place, seen
again from each frame that observed it, falls inside the box found there.

| TUM fr3 long office, 44 frames, 555 sightings | Before the one-frame rule | With it |
| --- | ---: | ---: |
| Items | 29 | 67 |
| Place falls inside the box it was seen in | 55.1% | 78.6% |
| Median distance from the box's middle, in box widths | 0.49 | 0.22 |
| Median `spread_m` | 0.108 m | 0.063 m |
| Most sightings joined into one item | 81 | 25 |

13 pairs of items with the same name lie within 12 cm of each other. Some are two things side
by side, some one thing whose sightings were not joined; the run does not tell them apart.

## Not built yet

Items drawn inside the scan's own splat or mesh; finding by what a thing looks like inside the
installed app (it has no PyTorch); camera paths for plain video (so shots share a world); masks
and tracking; the same thing joined across days.

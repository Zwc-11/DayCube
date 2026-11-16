# Items: the things in a recording, placed in 3D and found by words

`services/ingest/ingest/items/` turns a LiDAR scan or a plain video into items: each with a place,
a size, when it was seen, its best picture and a vector that words can be compared with. Nothing is
trained; three open models are used as released.

```
recording ─► frames (pose + depth) ─► detect (boxes, names, caption, text) ─► place (box → 3D)
          ─► embed (a vector per picture) ─► merge (same thing, one item) ─► store ─► find
```

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
stage, and the reprojection check.

- **A scan** shares one frame for the whole recording (ARKit's metres, y up): the same thing seen
  from several frames is one item, kept only when seen at least twice, and `spread_m` is how far its
  sightings lie from its place.
- **A plain video** has no camera path yet: each shot's things are placed in that shot's own camera
  by a depth model (`"depth": "model"`), and joined only within the shot.

Depth is labelled by how it was known: `lidar` (measured), `estimate` (ARKit's guess beyond the
LiDAR's 5.5 m) or `model`.

## API

`GET /items`, `GET /items/find?q=<words>` (by name, over the whole vault), `GET /items/{date}/{id}`,
`GET /items/{date}/{id}/crops/{name}`. Finding by what a thing looks like needs the embedder and is
in `items/search.py`; the app's core runtime has no PyTorch, so the API finds by name only.

## Checks

`services/ingest/tests/test_items.py` (the models stood in: geometry, merging, turning of sideways
frames, storage, the API). `eval/items_tum.py` runs the real models on the TUM RGB-D office
recording, whose camera path and depth are measured, and reports how often an item's place, seen
again from each frame that observed it, falls inside the box found there.

## Not built yet

Pins in the 3D viewer; word search inside the installed app; camera paths for plain video (so
shots share a world); masks and tracking; the same thing joined across days; `forget.py` does not
yet remove items made from a deleted span.

# Items: the things in a recording, placed in 3D and found by words

`services/ingest/ingest/items/` turns a LiDAR scan or a plain video into items: each with a place,
a size, when it was seen, its best picture and a vector that words can be compared with. Nothing is
trained; open models are used as released.

```
recording ─► frames (pose + depth) ─► detect (boxes, names, caption, text) ─► place (box → 3D)
          ─► embed (a vector per picture) ─► merge (same thing, one item) ─► store ─► find
```

## When

For a scan in the vault with its depth, the receiver queues the `scan_items` [job](jobs.md):
`ingest.items.run` in the 3D environment (`services/recon`, or the desktop app's 3D pack), on the
GPU when the PC is free, written to `vault/<date>/items/<a set id made from the scan's>`. Without
that environment the job waits and `capture doctor` says how to install it. A plain video is
run by hand, below: nothing files a video into a day yet.

## Run it

```
python -m ingest.items.run scan  <Stray Scanner capture folder> --out vault/<date>/items/it_<12 hex>
python -m ingest.items.run video <file.mp4>                     --out vault/<date>/items/it_<12 hex>
python -m ingest.items.run search "a wooden bench"              --out vault/<date>/items/it_<12 hex>
```

It needs PyTorch and `transformers` (the recon environment: `uv sync --project services/recon`);
weights download from the Hugging Face hub on first use. A GPU is not required: on two CPU cores a
frame's readings (its things, a caption, the writing in it) cost 32 to 46 CPU-seconds. The picture
goes through Florence-2's vision tower once for all of them, which took 39% off (53 to 72
CPU-seconds before, the same answers).

| Role | Model | Licence |
| --- | --- | --- |
| Boxes and names, captions | Florence-2 base | MIT |
| The writing in a picture | RapidOCR (PP-OCRv4, Chinese and English), in the recon environment; Florence-2 where it is missing | Apache-2.0 |
| Picture and text vectors | SigLIP 2 base/16-224 | Apache-2.0 |
| Depth where there is no LiDAR | Depth Anything V2 Metric Small (indoor, outdoor) | Apache-2.0 |
| The camera's poses through a video's shot | Depth Anything 3 Small, in its own environment | Apache-2.0 |

**The poses environment** (`uv sync --project services/poses`, 1.6 GB with the CPU build of
PyTorch) is optional and separate: the `depth-anything-3` package needs numpy 1 and OpenCV 4, the
rest of DayCube numpy 2 and OpenCV 5. `ingest/items/poses.py` runs in it as a child process that
loads the model once and stays for the whole video, answering a shot at a time; arrays pass
through a temporary folder. Depth Anything 3 Small and Base are Apache-2.0; its Large, Giant and
Nested weights are CC BY-NC and are not used.

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
- **A plain video** is taken shot by shot (cut where FFmpeg's scene score jumps), and each shot's
  things are placed in the camera of its keyframe, the sharpest look into it (`"frame": "shot"`,
  `"depth": "model"`). Shots are not placed against each other.
  - Without the poses environment a shot gets one look: the keyframe, with a phone's field of view
    (69°) and the depth model's metres.
  - With it a shot is looked at every second (2 to 24 looks, decoded in one run of the decoder
    from the first to the last). Depth Anything 3 gives each look's
    camera, its focal length and its depth, in a unit of its own; the keyframe's depth in metres,
    over the model's depth of the same pixels, says what that unit measures. The looks are then
    joined as a scan's frames are.
  - An item of such a shot is kept when the keyframe shows it or two looks do. What one other look
    alone shows is most often something that moved (a hand, someone walking through), which would
    otherwise be listed once per look.
  - The frame entry of a shot holds its caption and the writing read in its keyframe: a clock
    stamp and a caption burned into an edit are found there (`"text": "9:16 AM\n到公司啦"`).

Depth is labelled by how it was known: `lidar` (measured), `estimate` (ARKit's guess beyond the
LiDAR's 5.5 m) or `model`.

## API

`GET /items`, `GET /items/find?q=<words>` (over the whole vault), `GET /items/{date}/{id}`,
`GET /items/{date}/{id}/crops/{name}`. `find` returns the items whose names hold every word, then
those shown by a frame whose caption or writing does. Finding by what a thing looks like needs the
embedder and is in `items/search.py`; the app's core runtime has no PyTorch, so the API does not.

## In the viewer

`?view=items` (the box in the graph page's bar): the things of a recording as a list, most seen
first, with each one's best picture, and as balls in 3D where each was, as wide as the thing, on
a metre grid. Picking either shows the item's place, size, how well its sightings agree and how
its depth was known. The box above the list finds by any name the thing was given, or by the
caption and writing of its frame. A plain video's items are shown one shot at a time, since each
shot has its own camera, under the shot's time, the writing read in it and its caption
(`apps/viewer/src/components/items/`, `apps/viewer/e2e/items.spec.ts`).

## Checks

`services/ingest/tests/test_items.py` (the models stood in: geometry, merging, turning of sideways
frames, a shot's looks placed in its keyframe's camera, storage, the API). `eval/items_tum.py` runs the real models on the TUM RGB-D office
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

### A plain video

One edited day vlog (90.6 s, 1080×1920, a wide-angle action camera, 34 shots, a clock stamp and a
Chinese caption burned into 18 of them), on two CPU cores:

| | One look a shot | With poses (92 looks, 2 to 5 a shot) |
| --- | ---: | ---: |
| Items | 264 | 285 |
| Seen from two looks or more | 0 | 159 |
| Their place falls inside the box it was seen in (360 sightings) | | 85.3% |
| Median distance from the box's middle, in box widths | | 0.17 |
| Median `spread_m` | | 0.114 m |
| Seconds | 932 | 883 |

The check counts only items that were joined, and joining needs their places to lie near each
other, so it says the looks agree, not that the metres are right: those come from the depth
model. `eval/items_tum.py --poses` measures that part against a motion capture, below.

| The writing in the 18 stamped keyframes | Florence-2 `<OCR>` | RapidOCR |
| --- | ---: | ---: |
| Clock stamp read exactly | 17 | 18 |
| Chinese caption read exactly | 0 (it writes other characters) | 18 |
| Seconds a frame (two CPU cores) | about 8 | 0.6 |

`eval/items_tum.py --poses` gives the TUM recording to the same code as one shot of a plain
video: its 44 frames (2 s apart, a 20.7 m path around two desks) with no measured pose or depth.

| TUM fr3 long office, 44 frames | Measured path and depth | Both from the models |
| --- | ---: | ---: |
| Items | 67 | 69 |
| Place falls inside the box it was seen in | 78.6% | 71.4% |
| Median distance from the box's middle, in box widths | 0.22 | 0.31 |
| Median `spread_m` | 0.063 m | 0.107 m |
| A camera's distance from where the motion capture has it, nothing aligned | | median 0.38 m, worst 1.01 m |
| A camera's turn from the true one | | median 3.2° |
| The models' metre over the true one | | 1.13 |
| Focal length (true 535 px) | | 527 px |

Most of the cameras' error is the metre: the depth model's rooms are 13% too large here, and
every distance inherits that. A video's shot is far shorter than this path (2 to 5 looks, 1 s
apart).

The 18 stamps run from 7:44 AM to 6:22 PM. The day's [timeline](timeline.md) has one stretch
away from the desk, 07:43 to 19:19: every stamp falls inside it.

## Not built yet

Filing a video into a day (its shots as moments at the times its stamps give); a shot's own
pixels in 3D behind its items (the pose model's depth has them); shots placed against each other;
items drawn inside the scan's own splat or mesh; finding by what a thing looks like inside the
installed app (it has no PyTorch); masks and tracking; the same thing joined across days.

# Memories: photos you can step into

One photo becomes a small 3D scene that you step into: the viewer opens it as the flat photo and
fades into its 3D scene as the view moves a little way in.

## Making them

Drop photos into `vault\inbox\memories` and double-click `make-memories.cmd`.

- JPEG, PNG and iPhone HEIC are read.
- Each photo is filed under the day it was taken, from its EXIF time, else the file's.
- The first time, with nothing in the inbox, the sharpest frames of the 2026-10-24 trail scan are
  used instead.

`ingest/recon/memory.py`, in `services/recon`'s environment:

    uv run --project services/recon python -m ingest.recon.memory [--inbox] [--scan DATE --count N] [photo ...]

**How a photo becomes a scene:**

- **SHARP** (Apple, research licence) predicts 3D Gaussians in metres for what the photo shows, in
  one pass. It took about 6 s per photo on the RTX 5070.
- **Focal length:** from EXIF (the 35 mm equivalent, as phones write it). For a scan frame,
  ARKit's.
- **What is kept:** Gaussians at least 5% opaque, the most visible 1.5 M of them. They are written
  as the viewer's 32-byte splat records, in its axes: the photo's camera at the origin, looking
  down -z.

**Where a memory lives:** `vault/<day>/memories/<id>/`, holding:

- `photo.jpg`, at most 1600 px on its long side;
- `memory.splat`;
- `memory.json`, with size, focal length, field of view, how far the scene is, counts and
  timing;
- `original.<ext>`, for a photo from the inbox.

The API lists them at `GET /memories` and serves `photo.jpg` and `memory.splat` at
`GET /memories/{date}/{id}/{file}`.

## Seeing them

Open `http://127.0.0.1:3000/?view=memories`, or **Memories** on the calendar page.
`components/memories/MemoryView.tsx` renders the scene with Spark:

| Input | What it does |
| --- | --- |
| On opening | the photo fades into its scene as the view steps in by 12% of the scene's median distance |
| Mouse | shifts the point of view up to 12% of that distance sideways, keeping the scene's centre in view |
| Drag | looks around, 22° either way and 14° up or down |
| Wheel, arrows, WASD | step in (to 45%) or back (8%) |
| Double-click | returns to where the photo was taken |
| Escape | closes |
| Nothing for 3 s | the view drifts slowly |

The view is held this close to the photo on purpose. On the trail, a photo lifted to 3D gains
1–2 dB over itself up to about 1 m, and almost nothing by 4 m (`docs/glasses.md`, "One photo").
Beyond that it shows what the photo never saw.

## Licence

SHARP's weights are licensed for research, not for a commercial product. A product path is a
licensed depth model with one splat per pixel: Depth Anything V2 metric is Apache-2.0 per its
card. In the trail test it rendered about as well as SHARP did through the eval's approximate renderer, but its
metric scale was off by 3× there, so the scale has to come from elsewhere.

## Tests

- `services/ingest/tests/test_memory.py`:
  - OpenCV axes to the viewer's, positions and Gaussian rotations;
  - a memory's files and numbers, from a stand-in for SHARP;
  - inbox photos filed by their EXIF date and focal length;
  - the API's listing and file route, including refused names.
- `apps/viewer/e2e/memories.spec.ts`:
  - a memory written into the test vault opens from the list;
  - it reaches ready and draws a picture of many colours;
  - the mouse moves the view within its bound, and the step in stays within its bound;
  - Escape closes it.

# Memories: photos you can step into

One photo becomes a small 3D scene that you step into: the viewer opens it as the flat photo and
fades into its 3D scene as the view moves a little way in.

## Making them

Drop photos into `vault\inbox\memories`. The receiver makes them on its own
(`services/capture-win/capture/memories.py`); `make-memories.cmd` does the same by hand.

- Every minute the receiver looks in the inbox. Photos that have not changed for 10 s are made, in
  a child process of `services/recon`'s environment (no window, below-normal priority), its output
  appended to `vault\logs\memories.log`. SHARP is loaded only when there is a photo to make.
- JPEG, PNG and iPhone HEIC are read.
- Each photo is filed under the day it was taken, from its EXIF time, else the file's.
- A photo that cannot be read is moved to `vault\inbox\memories\failed`, the reason beside it in
  `<name>.txt`. A failure in SHARP itself (the GPU busy) leaves the photos in the inbox, and the
  receiver tries again 30 minutes later.
- With no memories in the vault yet, the sharpest frames of the newest scan whose capture is still
  there (`--scan latest`) are made into memories, once per receiver start.
- Unlike a scan's training, this does not wait for the PC to be free: it is about 6 s a photo.

`ingest/recon/memory.py`, in `services/recon`'s environment:

    uv run --project services/recon python -m ingest.recon.memory [--inbox] [--scan DATE|latest --count N] [--if-none] [photo ...]

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

A memory made from a scan's frame also records its `world`:

- the scan moment;
- the photo's camera pose in the scan's world (4 × 4, OpenGL axes, the frame of the scan's splat
  and `cameras.json`);
- `scale`, the scan's metres per SHARP metre. This is the median of the LiDAR's depth over the
  nearest Gaussian's, at reliable LiDAR pixels. On the trail SHARP's depth was about half the
  LiDAR's.

The API lists them at `GET /memories` and serves `photo.jpg` and `memory.splat` at
`GET /memories/{date}/{id}/{file}`.

## Seeing them

Open `http://127.0.0.1:3000/?view=memories`, or **Memories** on the calendar page.
`components/memories/MemoryView.tsx` renders the scene with Spark, one of two ways.

**In its place's world** (a photo from a scan):

- The scan's splat is the world around you: the trained splat, else the LiDAR's points.
- The memory stands where the photo was taken, at the LiDAR's scale.
- You start where the photo was taken, with its roll kept, so the view lines up with the photo.

| Input | What it does |
| --- | --- |
| Drag | looks all the way round, ±80° up and down |
| WASD or arrows | walk at 1.4 m/s (Shift ×3); Q and E move down and up |
| Wheel | 0.35 m forward or back a notch |
| Walking away | the photo's own scene fades out between 1.2 and 3 m from where it was taken, and the scan alone carries on |
| Clicking another photo of the scan | goes to it; each floats 0.9 m ahead of where it was taken, facing back |
| Double-click | returns to where the photo was taken |

**On its own** (any other photo): the space around it is a gradient of the photo's own top and
bottom colours, dimmed.

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
  - In a world (a room of splats as the scan):
    - it starts where the photo was taken, with the other photo as a marker;
    - walking 3.2 m fades the memory out;
    - dragging turns past 2 radians;
    - double-click returns, the memory sharp again.
- `test_memory.py` also covers the LiDAR scale (a wall SHARP put at 2 m and the LiDAR at 4 m
  gives 2), and the listing's world. A moment name that would leave its day is ignored.

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
`GET /memories/{date}/{id}/{file}`. With the vault token, `PATCH /memories/{date}/{id}` sets its
`caption` (at most 280 characters; empty removes it) and `PUT /memories/{date}/{id}/film-portrait.mp4`
or `film-landscape.mp4` keeps an exported film beside it (an MP4, at most 512 MB; the newest of
each shape wins). A memory taken in a span that is deleted goes whole, with its films
([deleting a span](capture.md#deleting-a-span)).

## Seeing them

Open `http://127.0.0.1:3000/?view=memories`, or **Memories** on the calendar page.
`components/memories/MemoryView.tsx` renders the scene with Spark, one of two ways.

**In its place's world** (a photo from a scan):

- The scan's splat is the world around you: the trained splat, else the LiDAR's points.
- The memory stands where the photo was taken, at the LiDAR's scale.
- You start where the photo was taken, looking where it looked, upright in the world (ARKit's y is
  up). An iPhone held upright records its scan's frames a quarter turn round (the trail's six were
  turned -88° to -97°): keeping that roll showed the world on its side. The field of view is the
  photo's upright height, so the turned frame's width (71.5° on the trail).
- The grid and the opening photo show a turned frame turned back (`photoRoll` in
  `lib/memories.ts`); the other photos' frames in the world are on planes turned with their
  cameras, so they are upright already.

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

## Film

The clapperboard on an open memory plays it as a film (`?view=memories&memory=<date>/<id>&film=1`,
`components/memories/MemoryFilm.tsx`, `lib/film.ts`), after a scan-driven particle reel: the
photo opens into its 3D scene, its subject is left floating, and its points stream back into the
photo, which ends on the date, the time and the caption.

**What happens, in seconds** (a photo on its own runs 15 s; one from a scan 19 s):

| Beat | Alone | From a scan | What you see |
| --- | --- | --- | --- |
| Open | 0.6-4.8 | 0.6-4.8 | The photo turns into its points, and each flies out along its own camera ray from its pixel to the distance SHARP gave it: SHARP made one Gaussian per pixel, so this is the photo becoming the scene it shows, not an effect laid over it. Near points go first. |
| Isolate | 4.6-7 | 4.6-6.6 | The background fades to a faint dust, smaller, and the near subject floats alone. |
| Grow | | 6.2-11.2 | The camera backs off and the scan's points rise from the floor up around the photo, standing where it was taken; the growing edge is lit. |
| Recall | 8.6-12 | 12.2-15.6 | A copy of the points streams into a card, each to its own pixel, top of the photo first. The scene stays and moves aside: right of the card in 16:9, above it in 9:16. |
| Card | 11.4-12.8 | 15-16.4 | The card becomes the photo, upright; then the date, the time and the caption. |

The corners carry real numbers: the timecode and frame, the Gaussians out of the photo, the
scene's distance, the scan's points and the height grown (metres, from the scan), and the photo's
time. The date takes the photo's liveliest colour.

**Subject and background.** The split is Otsu's threshold on the points' log distances, kept only
where the histogram has a valley between two peaks (under a third of the lower) and the near part
is over 5% of the points. A photo whose distances run on, a wall going away or a landscape, has no
split, and the film keeps all of it.

**Time.** An inbox photo's EXIF time (the PC's wall time), else a scan frame's seconds into its
scan; else the day the memory is filed under.

| Input | What it does |
| --- | --- |
| Drag | looks round while it plays (±10°, from a scan ±70°), easing back when let go |
| Space | pause or play; at the end, play again |
| R | from the start |
| Pen | the caption, kept with the memory (needs the vault token) |
| Frame | 16:9 or 9:16 (TikTok, Reels): the screen shows the frame Export writes |
| MP4 | Export: 1920 × 1080 or 1080 × 1920, 30 fps, 14 Mb/s |
| Escape | back to the memory |

**How it is drawn.** All points, by the film's own shader (`lib/film.ts`), read straight from the
`.splat` files: at most 700,000 of the memory's and 600,000 of the scan's, an even stride through
them, each drawn twice its Gaussian's mean scale (more for a sample, so it covers as the whole).
The memory's points are sorted far to near once (a counting sort) and blend right from its view
without a sort per frame. The film does not use Spark: Spark sorts its Gaussians off the main
thread, so a frame would depend on when its sort came back. Here each frame depends on its time
alone, which is what lets Export draw the frames the screen does. A held frame is not drawn again
until something changes (a drag, a resize, a new caption).

**Export.** Frame by frame, not a screen recording: each frame is drawn at the film's size, the
scene and the 2D layer (the card, the words, the corners) put together, and encoded with WebCodecs
through Mediabunny (H.264, else VP9, else AV1, in MP4). A slow GPU takes longer; it drops no
frame. The film downloads, and with the vault token is kept beside the memory. In software
rendering (the tests' Chromium) a 15 s 1080 × 1920 film took 206 s.

**Limits.** One photo holds up only near its own view, so a photo alone swings 7° either way and
steps in 10%; one from a scan swings 16° once the scan is around it. SHARP's distances are its
own guess: the split is as good as they are.

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
  - the API's listing and file route, including refused names;
  - a caption kept and removed, an exported film kept and served, and what is refused (no token,
    not an MP4, another file name, a memory that is not there).
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
- The film (`apps/viewer/e2e/memories.spec.ts`, `scripts/film.test.mjs`):
  - the Film button opens it; it finds the near blobs and the wall as two, and the wall's colours
    go as it fades;
  - at the end the card draws the photo; a caption typed is kept in `memory.json`; Space holds
    it; Escape goes back;
  - Export writes an MP4 of 450 frames (15 s at 30 fps, none dropped), downloaded and kept beside
    the memory;
  - a photo from a scan grows the room's 32,805 points;
  - points read far to near without faint ones, a sample sized to cover; the split found for a
    subject and its background and refused for an even run, a speck and too few points; the card's
    place in 16:9 and 9:16; the beats in order; the timecode and date.
- `test_forget.py`: a memory taken in a deleted span goes, and so does a frame of a recording that
  went, though the frame itself is just after the span.
- `test_memory.py` also covers the LiDAR scale (a wall SHARP put at 2 m and the LiDAR at 4 m
  gives 2), and the listing's world. A moment name that would leave its day is ignored.

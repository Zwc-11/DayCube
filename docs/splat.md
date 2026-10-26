# Scans in 3D (Gaussian splats)

A LiDAR scan (Stray Scanner, [phone.md](phone.md)) becomes a 3D scene you can walk through in the
viewer. There are two stages, and the viewer shows the best one available:

1. **LiDAR preview** (minutes, no GPU): the LiDAR's own returns as coloured dots, plus ARKit's
   estimates past the LiDAR's range as larger ones.
2. **Trained splat** (tens of minutes on the GPU): 3D Gaussian splats trained from the video's
   frames, the way the walk looked.

    uv run daycube splat --date 2026-10-24              # every scan of the day: preview, then training
    uv run daycube splat --date 2026-10-24 --no-train   # the preview only
    uv run daycube splat --date 2026-10-24 --moment mo_scan_20261024_202749 --steps 30000 --frames 1260

## The training set (`ingest/splat/dataset.py`)

No structure from motion is run. ARKit already knows where the camera was for every frame
(`odometry.csv`: metres, gravity aligned, y up).

- **Poses.** Stray Scanner writes them in the OpenCV camera convention: x right, y down, looking
  along +z. On 2026-10-24 the +z axis pointed along the walk (cosine 0.93) and -z against it.
  nerfstudio and Brush read OpenGL's convention (y up, looking along -z), so the camera's y and z
  axes are flipped when the file is read. The same fix corrected the scan's heading on the map,
  which had been 180 degrees off.
- **Frames.** The video is decoded once at 480 px to score sharpness (variance of the Laplacian).
  The sharpest frame of each of 1,260 equal windows is kept (one every 13 cm on 2026-10-24), skipping the first half second, before
  ARKit's tracking settles (on 2026-10-24 the first pose looked 34 degrees down, a second later 8).
  Each kept frame is written at 1440 px with its own intrinsics into `transforms.json`.
- **Starting points.**
  - Within 5.5 m, each kept frame's depth map (256 x 192, millimetres) is back-projected where
    confidence is medium or high. The points are coloured from the frame and merged on a voxel
    grid that coarsens until at most 1.2 M points remain (3.8 cm on 2026-10-24).
  - From 5.5 m to 15 m, a quarter of the depth pixels, at any confidence, are merged on a 10 cm grid.
    They seed the trees and walls the LiDAR cannot reach.
  - Brush starts from these points instead of random splats, which is what lets a forward walk,
    with little parallax, train.
- **Sky shell.**
  - Brush renders on a black background and starts only from these points. Without anything past
    15 m, the sky between the trees had to be learned from nothing.
  - On 2026-10-24, with 420 frames, held-out PSNR fell from 14.7 to 13.5 dB while the training views
    were fitted.
  - Points on a sphere three times the walk's reach from its middle (239 m), above −10°, are now
    added. Each is coloured from the kept frame that looks most directly at it (128,000 points).
- **Check, before any GPU time.** Depth pixels of one kept frame are carried into a frame about
  0.9 s later.
  - Their predicted depth must match the LiDAR's there to within 3%.
  - Their colours must match that frame clearly better than with the poses read in the mirrored
    convention (under 85% of its colour error). Over flat ground, geometry alone cannot tell walking
    forward from walking backward; the picture can.

  On 2026-10-24:

  | Reading | Depth error | Colour error | Wrong-pose error | Mirrored colour error |
  | --- | --- | --- | --- | --- |
  | OpenGL, as converted | 1.8% | 29 | 12.8% | 41 |
  | Stray's poses read as already OpenGL | 23% | 41 | | 26 |

  The second reading fails both tests.

## The mesh (`ingest/splat/mesh.py`)

`daycube mesh --date <day>` (with the `mesh` extra: `uv sync --extra mesh`), or the receiver's
`scan_mesh` job (Open3D is one of the receiver's dependencies):

- **Fusion.** Every third depth map (medium or high confidence, within 5 m) is fused into a
  truncated signed distance field at ARKit's poses, using Open3D's tensor `VoxelBlockGrid` with
  4 cm voxels. Its legacy `ScalableTSDFVolume` fused nothing in the 0.20 wheel. Colours come from
  the video at the depth maps' size.
- **Cleanup.** Islands under 400 triangles are dropped, and the mesh is simplified to at most
  600,000 triangles.
- **Output.** `moments/<id>/mesh/scene.glb`, with a `mesh` entry in `scene.json`.
- **Check.** LiDAR points of depth maps that were not fused are measured against the mesh. On
  2026-10-24:
  - 600,000 triangles, from 958,625
  - median distance 1.6 cm, 90th percentile 4.3 cm
  - 75 s on two CPU cores
- **In the viewer.** **Mesh** sits beside Splat and LiDAR. It is drawn unlit in the video's colours,
  and it follows the walk the same way.

## Training (`ingest/splat/brush.py`)

[Brush](https://github.com/ArthurBrussee/brush) 0.3.0 (Apache-2.0) trains the splats. It is a Rust
trainer that reaches the GPU through WebGPU (DX12 or Vulkan). That means the RTX 5070 (Blackwell,
sm_120) needs no CUDA toolkit and no compiler. gsplat's prebuilt Windows wheels stop at PyTorch 2.4
with CUDA 12.4, which cannot run on sm_120.

The release is pinned by version and SHA-256. It is downloaded once into `%LOCALAPPDATA%\DayCube\tools`
(override with `DAYCUBE_TOOLS`), or set `DAYCUBE_BRUSH` to point at your own Brush. Brush runs
headless with these settings:

- 30,000 steps
- 1440 px frames
- spherical harmonics of degree 3
- at most 4 M splats
- every 25th frame held out to measure the fit (PSNR)

On 2026-10-24 (165 m, a forest track, RTX 5070, 30,000 steps, 4 M splats):

| Run | Frames | Sky shell | Held-out PSNR (start → end) | SSIM (end) | Minutes |
| --- | --- | --- | --- | --- | --- |
| 1 | 420 | no | 14.7 → 13.5 dB, falling from step 3,000 | 0.17 | 38 |
| 2 | 1,260 | yes | 15.5 → 15.3 dB, highest 15.6 at step 9,000 | 0.20 | 36 |

- **Reference points.** Copying the neighbouring frame, 0.4 m away, scores 10.2 dB; a black frame
  scores 6.1 dB.
- **Why it is low.** Thin foliage seen from a forward walk is the hard case for Gaussian splats, and
  Brush 0.3.0 cannot use the LiDAR's depth as a loss.
- **Default.** More frames per metre held the overfitting back, so 1,260 frames is now the
  default.

Its log goes to `vault/derived/splat/<date>/<moment>/brush.log`. Progress is printed every few
seconds, with steps, splats, minutes and PSNR.

## What is written

| Path | What |
| --- | --- |
| `vault/derived/splat/<date>/<moment>/train/` | `transforms.json`, `init.ply`, `lidar.ply`, `far.ply`; frames removed after training |
| `.../dataset.json` | frames, size, path length, extent, starting points, the check |
| `.../export/splat.ply` | the trained splat, with view-dependent colour |
| `moments/<id>/splat/lidar.splat` | the preview (32-byte `.splat` records, most visible first) |
| `moments/<id>/splat/scene.splat` | the trained splat for the viewer (without the higher spherical harmonics, and without splats that reach the walked path: the phone passed through that space, and a renderer draws a splat around the camera across the whole view; 5,578 of 3 M on 2026-10-24) |
| `moments/<id>/splat/cameras.json` | where the camera was every 3 frames, for following the walk |
| `moments/<id>/scene.json` `splat` | `status` (`lidar`, `training`, `trained`, `failed`), files, counts, bounds, training figures, error |

- **Importer.** Since 2026-10-25 the importer keeps each scan's `depth/`, `confidence/`,
  `odometry.csv` and `camera_matrix.csv` in `moments/<id>/capture/`, so a scan can be trained
  without its original folder. An older scan's capture is found in `vault/inbox/phone/imported/`,
  matched by the video's name and size, or can be given with `--capture`.
- **Re-import.** Importing the same capture again keeps `splat/` and the `splat` entry.

## Without a command ([jobs.md](jobs.md))

The capture receiver (`.\capture`) makes the 3D itself, as two jobs:

- **`splat_preview` (CPU).** A scan with its depth and no 3D yet gets its LiDAR preview within a
  minute or two of being imported.
- **`splat_train` (GPU).** The preview leads to training, one scan at a time, while the PC is free:
  no keyboard or mouse input for 10 minutes, or between 01:00 and 07:00.
  - Training waits until Brush is installed (the first `daycube splat` installs it) or
    `DAYCUBE_SPLAT_TRAIN=1` is set. The receiver never downloads a trainer on its own.
  - A failed training is tried once more, then waits for `daycube jobs retry --stage splat_train`.
    The reason is in the moment's `scene.json` and in doctor.

## In the viewer

**On the day's stage.** A visit with a scan shows the scan itself in Place view, in place of the
diorama drawn for every place of its kind (`components/diorama/ScanStage.tsx`):

- **The scene.** The trained splat; else the mesh; else the LiDAR's points. A scan with none of
  them (no depth folder) leaves the visit its diorama.
- **The time.** The camera walks where the phone walked at the timeline's time. Playing or
  scrubbing the day plays the walk. Before the scan it stands at the walk's start, after it at
  the end, and the caption says the timeline is outside the scan.
- **Switching.** **3D scan** and **Diorama**, top left. **Video and scenes** opens the scan's own
  view below. The scan's card in the side panel says **Open in 3D**.
- **One scene at a time.** While the scan's own view is open, the stage lets its scene go, so two
  copies of a 3 M splat scene are never on the GPU at once.

**The scan's own view.** Double-click a scan, or **Open in 3D** on its card:

- **Opening.** With a scene, the scan opens in **3D**, and the recorded video plays in a corner.
- **Following.** The camera follows the walk at the video's time. It stands where the phone was,
  looks where it looked, and stays level whichever way the phone was held.
- **Looking around.** Dragging or scrolling switches to free looking. **Follow the walk** goes back.
- **Choosing a scene.** **Splat** and **LiDAR** switch between the trained scene and the preview.
  **Video** shows the video full size again.
- **Capture panel.** It says what the scene is, for example "1,391,603 LiDAR points; not trained
  yet", or the splat count, the training minutes and the PSNR on frames it never saw.

**The renderer** is [Spark](https://sparkjs.dev) 2.3 (World Labs, MIT) on the viewer's three.js
0.186. It loads only when a scan opens in 3D.

- **Why not GaussianSplats3D.** GaussianSplats3D 0.4.7 lagged on the 3 M-splat trail. It sorted
  every splat on the CPU each frame without shared memory, and its README says it is no longer in
  active development and recommends Spark.
- **Level of detail.** Spark builds a splat tree in a worker as a scene loads, and draws at most
  2 M splats a frame, the nearest finest (`LOD_SPLATS`). Scenes under 500,000 splats are drawn
  whole: Spark 2.3's tree builder fails on some tiny ones, and if it fails on a large one the view
  draws every splat instead.
- **Centres in float32** (`extSplats`). In float16, centres 100 m from the origin fall on a grid of
  6–12 cm.
- **Fill rate.** No MSAA, and at most 1.25 device pixels per CSS pixel.
- **The camera every frame.** Following, the view reads the time each frame: the video's
  `currentTime` in the scan's own view, and on the day's stage the playhead carried on between the
  app's 10 Hz ticks, as the diorama's walker does. Before, the camera moved only when the time
  arrived: 10 times a second with the day playing, about 4 with the video (`timeupdate`).
- **The walk at its own speed.** While the stage shows a scan, playback crosses the scan's span at
  real time × the speed (1×, 4×, 12×), and stops at its start rather than jumping over it. At the
  day's pace the 10 s test scan went by in two frames.
- **At rest when still.** Frames are drawn only for 600 ms after the camera, the time, the size or
  Spark's sort and detail last changed, and every frame while the scene loads.

### Measuring on the GPU

The end-to-end tests draw in software (about 8 frames a second), so they check behaviour, not
speed. `check-3d.cmd` on the PC runs `scripts/profile-splat.mjs`. It starts its own viewer on port
3100 (and its own API on 7711 when none answers on 7701), opens the day's scan in headed Edge with
unthrottled frames, and measures:

- following: 10 s of the day playing over the scan at 4×, frame intervals and the camera's step
  each frame
- orbiting: 5 s of dragging
- at rest: frames drawn in a second of stillness

It writes `vault/logs/splat-perf-<label>.json`. It passes when:

- the browser drew with the NVIDIA GPU
- following has p95 ≤ 8.3 ms and p99 ≤ 16.7 ms
- there is no long task over 50 ms
- no frames are drawn at rest

## Tests

- `services/ingest/tests/test_splat.py` uses a scan ray-cast from a textured ground and a recessed
  wall. It checks:
  - the training set, with OpenGL poses and the points on the surfaces they came from
  - that the check refuses the mirrored convention
  - the heading
  - the `.splat` records
  - the whole run on a moment with a stand-in for Brush (preview, then trained; survives
    re-import; a failed training keeps the preview and says why)
  - reading Brush's log
- `apps/viewer/e2e/phone.spec.ts` checks:
  - on the day: a visit with a scan shows it on the stage, the camera at the walk's start before
    the scan and at its end after it, switching to the diorama and back; a scan with no scene
    keeps the diorama
  - the 3D view opens by default
  - the camera stands where the phone was at the video's time
  - dragging switches to looking around, and following resumes

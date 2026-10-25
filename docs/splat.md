# Scans in 3D (Gaussian splats)

A LiDAR scan (Stray Scanner, [phone.md](phone.md)) becomes a 3D scene you can walk through in the
viewer. There are two stages, and the viewer shows the best one available:

1. **LiDAR preview** (minutes, no GPU): the LiDAR's own returns as coloured dots, plus ARKit's
   estimates past the LiDAR's range as larger ones.
2. **Trained splat** (tens of minutes on the GPU): 3D Gaussian splats trained from the video's
   frames, the way the walk looked.

    uv run daycube splat --date 2026-10-24              # every scan of the day: preview, then training
    uv run daycube splat --date 2026-10-24 --no-train   # the preview only
    uv run daycube splat --date 2026-10-24 --moment mo_scan_20261024_202749 --steps 30000 --frames 420

## The training set (`ingest/splat/dataset.py`)

No structure from motion is run. ARKit already knows where the camera was for every frame
(`odometry.csv`: metres, gravity aligned, y up).

- **Poses.** Stray Scanner writes them in the OpenCV camera convention: x right, y down, looking
  along +z. On 2026-10-24 the +z axis pointed along the walk (cosine 0.93) and -z against it.
  nerfstudio and Brush read OpenGL's convention (y up, looking along -z), so the camera's y and z
  axes are flipped when the file is read. The same fix corrected the scan's heading on the map,
  which had been 180 degrees off.
- **Frames.** The video is decoded once at 480 px to score sharpness (variance of the Laplacian).
  The sharpest frame of each of 420 equal windows is kept, skipping the first half second, before
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

Its log goes to `vault/derived/splat/<date>/<moment>/brush.log`. Progress is printed every few
seconds, with steps, splats, minutes and PSNR.

## What is written

| Path | What |
| --- | --- |
| `vault/derived/splat/<date>/<moment>/train/` | `transforms.json`, `init.ply`, `lidar.ply`, `far.ply`; frames removed after training |
| `.../dataset.json` | frames, size, path length, extent, starting points, the check |
| `.../export/splat.ply` | the trained splat, with view-dependent colour |
| `moments/<id>/splat/lidar.splat` | the preview (32-byte `.splat` records, most visible first) |
| `moments/<id>/splat/scene.splat` | the trained splat for the viewer (without the higher spherical harmonics) |
| `moments/<id>/splat/cameras.json` | where the camera was every 3 frames, for following the walk |
| `moments/<id>/scene.json` `splat` | `status` (`lidar`, `training`, `trained`, `failed`), files, counts, bounds, training figures, error |

- **Importer.** Since 2026-10-25 the importer keeps each scan's `depth/`, `confidence/`,
  `odometry.csv` and `camera_matrix.csv` in `moments/<id>/capture/`, so a scan can be trained
  without its original folder. An older scan's capture is found in `vault/inbox/phone/imported/`,
  matched by the video's name and size, or can be given with `--capture`.
- **Re-import.** Importing the same capture again keeps `splat/` and the `splat` entry.

## Without a command (`ingest/splat/queue.py`)

The capture receiver (`.\capture`) makes the 3D itself, two minutes after it starts and then every
five minutes:

- A scan with its depth and no 3D yet gets its LiDAR preview at once (CPU, a minute or two).
- A scan with only the preview is trained, one at a time, when training is allowed and the PC is
  free: no keyboard or mouse input for 10 minutes, or between 01:00 and 07:00. Training is allowed
  once Brush is installed (the first `daycube splat` installs it) or with `DAYCUBE_SPLAT_TRAIN=1`.
  The receiver never downloads a trainer on its own.
- A failed training is not retried until `daycube splat` is run again. The reason is in the
  moment's `scene.json`.

`vault/derived/splat/status.json` holds what is waiting, what is running, what failed and what was
last done. `.\capture doctor` shows it on its `3D scans` rows.

## In the viewer

Double-click a scan on the day:

- **Opening.** With a scene, the scan opens in **3D**, and the recorded video plays in a corner.
- **Following.** The camera follows the walk at the video's time. It stands where the phone was,
  looks where it looked, and stays level whichever way the phone was held.
- **Looking around.** Dragging or scrolling switches to free looking. **Follow the walk** goes back.
- **Choosing a scene.** **Splat** and **LiDAR** switch between the trained scene and the preview.
  **Video** shows the video full size again.
- **Capture panel.** It says what the scene is, for example "1,391,603 LiDAR points; not trained
  yet", or the splat count, the training minutes and the PSNR on frames it never saw.

The renderer is [GaussianSplats3D](https://github.com/mkkellogg/GaussianSplats3D) 0.4.7 (MIT) on
the viewer's three.js 0.168. Spark needs three.js 0.180. It loads only when a scan opens in 3D. It
sorts in floating point, because integer sorting overflows on a 150 m scene, and without shared
memory, which would need cross-origin isolation.

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
  - the 3D view opens by default
  - the camera stands where the phone was at the video's time
  - dragging switches to looking around, and following resumes

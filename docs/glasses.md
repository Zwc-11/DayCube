# Glasses walks, simulated from a phone scan

Meta's glasses see but do not track: the Wearables Device Access Toolkit gives photos (4032×3024),
a 720p stream and the IMU's orientation, but no position. The plan
(`claude/glasses-3d-time-machine.md`, Phase 2) rebuilds a glasses walk from its images alone,
pins it to the iPhone's GPS, and trains it into splats.

Before any glasses or app, `eval/glasses_sim.py` measures how well that works on a walk whose true
camera path is known: a Stray Scanner scan, where ARKit gives the phone's pose for every frame.

## What it does (`ingest/recon`)

**1. Cut the video as glasses would take it** (`simulate.MODES`):

| Mode | Frames | Size | SIFT features |
| --- | --- | --- | --- |
| `photo_3s` | a photo every 3 s | the video's (1920×1440) | 8,192 |
| `photo_1s` | a photo every second | 1920×1440 | 8,192 |
| `stream_5fps` | the live stream, 5 frames a second | 1280×960 (720p's size) | 4,096 |

- **Photos are sharp.** A photo is the sharpest video frame within 0.15 s of its time, since a
  glasses still is exposed on its own. Stream frames are taken as they come, blur and all.
- **Matching cost.** Matching costs the square of the features. At 8,192, the stream's 642 frames
  matched at 5.5 s a frame on the PC's CPU; frames 25 cm apart need fewer.

**2. Rebuild from the images alone** (`sfm.py`, COLMAP 4.2 through pycolmap, BSD):

- SIFT features in every image, one shared pinhole camera.
- Each image matched with the next 10 (and, quadratically, a few further on).
- Incremental mapping; `--mapper global` uses GLOMAP. The largest model is kept.
- **The first pair of images.** COLMAP's defaults refuse a first pair whose camera moved mostly
  along its own view (`init_max_forward_motion` 0.95) and ask for 16° between its rays. Every pair
  of a walk looking ahead is refused. Here any motion is allowed, with 4°.
- pycolmap installs from pip on Windows and Linux and runs on the CPU.
- **Learned features** (`--features aliked`, `learned.py`). ALIKED keypoints matched by LightGlue
  (Kornia; ALIKED BSD-3, LightGlue Apache-2.0), on the GPU when there is one. They are written
  into the COLMAP database, and COLMAP verifies and maps them as its own. The pip build of
  pycolmap has no ONNX, so COLMAP's own ALIKED and LoMa are not there; this runs them in PyTorch,
  in `services/recon`'s environment (the speech worker's torch 2.9.1+cu128, linked from uv's
  cache).

**3. Score against the phone's path** (`align.py`):

- A similarity fit (Umeyama) from the rebuilt camera centres to ARKit's.
- Reported:
  - the share of images placed
  - the stretch of the walk the largest model covers (metres and share)
  - the path error (RMS, 90th percentile, largest; metres)
  - the cameras' turn error (degrees)

**4. Put it on the map with GPS only**:

- Gravity in each camera, which the glasses' IMU gives (here taken from ARKit), turns the
  reconstruction upright.
- A 2D similarity fits its path seen from above to the day's GPS, searching ±20 s for the capture
  time. Glasses stamp a photo when it reaches the phone, not when it was taken.
- Reported: the residual, the offset found, and the scale's error against the phone's path. The
  scale error is how metric a glasses walk is without LiDAR.

**5. Train splats** (`--train`). Brush trains each mode twice on its placed images, 7,000 steps
each:

- **Rebuilt:** the rebuilt poses, in metres from the GPS scale, with COLMAP's points as the start.
- **Phone:** the phone's own poses and intrinsics, the best those images could do.

Both are scored on the held-out frames that no photo mode trains on (24 over the walk), keeping
only those inside the stretch the model covers. Brush reads them from `transforms_val.json`.

- Each frame is given its true pose, carried into each reconstruction's frame by the fit.
- With fewer than 4 inside, training is skipped.
- The difference (`psnr_cost_db`) is what the rebuilt poses cost.

## Running it

On the PC, double-click `check-glasses.cmd`. It runs:

    uv run --project services/capture-win python eval/glasses_sim.py --train

`check-glasses-learned.cmd` sets up `services/recon` (once) and runs the same with
`--features aliked`. Its report is `vault/logs/glasses-sim-aliked.json`. Once that environment
exists, `update.cmd` keeps it in sync.

It uses the day's first scan (`--date 2026-10-24` by default). It writes:

- `vault/logs/glasses-sim.json`, the numbers only, rewritten after each mode
- `vault/logs/glasses-sim.log`
- the frames, COLMAP models, training sets and splats under
  `vault/derived/glasses-sim/<date>/<moment>/`, which stay on the PC

**Checks per mode** (the plan's):

- placed ≥ 90%
- path error ≤ 0.5 m RMS
- GPS residual ≤ 3 m
- scale within ±5%

## First run (2026-10-26, the 2026-10-24 trail)

The walk was 164.8 m in 129 s, with 3,862 frames and 1,324 GPS fixes. Its GPS came from the day's
`skeleton.geojson`, since the day was imported before Sensor Logger recordings were kept. This run
picked photos at their exact times, not the sharpest frame near them.

**`photo_3s`** (43 photos, about 3.8 m apart): nothing placed.

- Neighbours matched in a chain: 41 pairs, 31 photos connected.
- No pair had enough inliers to start a model, even with relaxed constraints.

**`photo_1s`** (129 photos, about 1.3 m apart): 16 placed (12%), in 3 models.

- The largest covers 17.8 m of the walk.
- Where it exists, it is accurate: 4 cm path RMS, 1.65° median turn, focal 1,316 px against the
  true 1,334.
- The GPS fit over 17.8 m is not reliable: a +3 s offset where the truth is 0, and a −5.4% scale.
- PSNR was 9.2 dB both ways. It is not meaningful: the 24 held-out frames were spread over the
  whole walk, most outside the model. Held-out frames are now limited to the model's stretch.

**`stream_5fps`**: stopped while matching (at 8,192 features, 5.5 s a frame); now 4,096.

**Second and third runs** (sharp stills, then forward motion allowed): `photo_3s` still placed
nothing.

- Most neighbouring photos 3.8 m apart had no SIFT match at all, from about 17,000 features each:
  157 of 195 pairs were degenerate.
- `photo_1s`, with sharp stills and COLMAP's defaults: 19 of 129 placed, covering 25.5 m.
  - path RMS 0.24 m
  - GPS residual 0.33 m, scale +3%
  - PSNR 14.8 dB rebuilt against 15.3 dB with the phone's poses, on the 4 held-out frames inside

- `photo_1s`, forward motion allowed (third run): 18 of 129 placed, in 5 models; the largest covers
  24.0 m of 164.8.
  - path RMS 5 cm, median turn 2.0°
  - GPS residual 0.29 m, offset −1 s (truth 0), scale +3.4%
  - PSNR 15.2 dB rebuilt against 15.3 dB with the phone's poses (4 held-out frames): the rebuilt
    poses cost 0.1 dB where they exist
  - SIFT matching took 294 s of the 358 s

**So far:** occasional stills of a tree-lined trail do not reconstruct with SIFT. The learned
matcher (ALIKED + LightGlue, `check-glasses-learned.cmd`) is the next run. After it, if needed:

- COLMAP's LoMa, from the COLMAP Windows CUDA release
- MapAnything, the plan's feed-forward option

## What it does not show

- **Optics.** These are the phone's frames, not the glasses': a different lens and sensor, and no
  4032×3024 stills. Photos at the video's 1920×1440 understate what glasses stills would give.
- **Blur.** The phone's frames are video frames, with their motion blur. Glasses stills are
  exposed on their own.
- **Gravity.** It is ARKit's here. The glasses' IMU orientation has a known 90° bug on glasses
  without a display (DAT known issues).
- **Head motion.** A head turns more than a hand-held phone.

## Tests

`services/ingest/tests/test_recon.py` uses a curved 24 m walk between two walls, ray-cast with a
texture that never repeats. Its poses are written as Stray Scanner writes them, and a GPS track
runs 3 s off the video's clock. It checks:

- the similarity fit and gravity levelling
- the frame picks: photos at their times; held-out frames no photo mode uses
- images alone, a photo every 2 s (2 m apart) and a 0.4 s stream:
  - ≥ 90% placed
  - path error under 10 cm and turn error under 1°
  - the 3 s clock offset found: to a second from the photos, exactly from the stream
  - GPS residual under 1 m and scale within 4%
- with PyTorch (`uv run --project services/recon pytest ...`): ALIKED + LightGlue place photos
  2 m apart, path under 15 cm; the pairs are the next few and quadratically further
- the training sets, with a stand-in for Brush:
  - in metres both ways
  - each held-out camera where it stands among the training cameras in both
  - the rebuilt cameras upright

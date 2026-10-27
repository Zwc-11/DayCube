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
- `stream_5fps` (third run, 642 frames at 1280×960, 4,096 features): 245 placed (38%), in 5 models;
  the largest covers 67.5 m, but it is wrong.
  - path RMS 6.8 m (p90 11.0 m), median turn 18°, p90 130°: parts of the model are folded or
    turned around, while its reprojection error is a normal 0.75 px
  - GPS residual 4.8 m, offset +13 s, scale +24.5%
  - PSNR 11.1 dB rebuilt against 14.9 dB with the phone's poses (10 held-out frames): 3.8 dB lost
  - features 124 s, matching 732 s, mapping 355 s

**Stream with SIFT, why it is wrong.** The rebuilt and phone positions of its 245 frames were
compared frame by frame:
- The error grows steadily from the middle of the model to both ends (10 m at each end, under 1 m in
  the middle), so the model is bent along the walk rather than broken in one place.
- Between frames 160 and 190 of the model, consecutive frames are 2.3–5.4 m apart where the
  phone moved 0.3 m.
- The likely causes are scale drift over 5 fps steps of 25 cm, and wrong matches with
  far-off frames: quadratic overlap pairs a frame with others up to 100 s later, on foliage that
  repeats.

### Fourth run: learned features (2026-10-26, 95f0ad7, `check-glasses-learned.cmd`)

ALIKED + LightGlue on the RTX 5070, the same frames, mapper settings and scoring:

| | `photo_3s` SIFT | `photo_3s` learned | `photo_1s` SIFT | `photo_1s` learned |
| --- | --- | --- | --- | --- |
| placed | 0 / 43 | 29 / 43 (67%) | 18 / 129 (14%), 5 models | 115 / 129 (89%), 1 model |
| walk covered | — | 157.7 m (96%) | 24.0 m | 164.7 m (100%) |
| path RMS (p90, largest) | — | 1.05 m (1.56, 2.78) | 0.05 m | 1.09 m (1.11, 8.33) |
| median turn error | — | 2.1° | 2.0° | 2.4° |
| GPS residual, offset, scale | — | 1.66 m, +5 s, +2.9% | 0.29 m, −1 s, +3.4% | 1.48 m, +4 s, +2.7% |
| PSNR rebuilt / phone poses | — | 12.7 / 12.8 dB (23 held out) | 15.2 / 15.3 dB (4) | 13.2 / 14.5 dB (24) |
| features + matching + mapping | 18 + 87 + 0 s | 13 + 26 + 6 s | 57 + 294 + 7 s | 12 + 95 + 66 s |

- Every neighbouring pair of `photo_3s` matched (510 of 510 pairs, 58k matches). With SIFT most
  had no match.
- The whole walk is one model from photos a second apart, and nearly all of it from photos 3 s
  (3.8 m) apart.
- The path error is about 1 m RMS over 160 m (0.7%). That is over the plan's 0.5 m, though under
  the phone GPS's own 2.0 m residual against ARKit (`phone_fit`).
- Scale is within 3% from GPS alone. The clock offset is found within 5 s, where the truth is
  0 s.
- Checks: `photo_1s` passes GPS ≤ 3 m and scale ±5%, and misses ≥ 90% placed by one point
  (89.1%) and path ≤ 0.5 m. `photo_3s` passes GPS and scale only.

**The stream** (642 frames at 1280×960, 5 fps):

| | SIFT (third run) | learned (fourth run) |
| --- | --- | --- |
| placed | 245 / 642 (38%), 5 models | 642 / 642 (100%), 1 model |
| walk covered | 67.5 m, bent | 164.8 m (100%) |
| path RMS (p90, largest) | 6.79 m (11.0, 13.7) | 1.14 m (1.60, 2.65) |
| median / p90 turn error | 18° / 130° | 2.0° / 4.0° |
| GPS residual, offset, scale | 4.81 m, +13 s, +24.5% | 1.65 m, +4 s, +2.6% |
| PSNR rebuilt / phone poses (held-out frames) | 11.1 / 14.9 dB (10) | 13.0 / 15.6 dB (24) |
| features + matching + mapping | 124 + 732 + 355 s | 26 + 466 + 1,382 s |

- Every frame is placed, in one model over the whole walk. The stream passes three of the four
  checks: placed, GPS and scale.
- **The remaining path error is smooth.** Frame by frame, the error rises and falls slowly along
  the walk: 2.1 m at the start, 0.1 m at frame 175, 1.5 m at 325, 0.4 m at 500, 2.3 m at the
  end. No consecutive frames jump. That is drift, in the rebuild, in ARKit's own tracking, or in
  both.
  - ARKit's path fits the GPS at 2.01 m (`phone_fit`); the rebuilt paths fit it at 1.48–1.66 m.
  - So the 0.5 m check against ARKit may be stricter than ARKit's own accuracy over 165 m.
    Telling the two apart needs a better reference than either.
- **Mapping is slow.** Incremental mapping took 23 minutes on the CPU: bundle adjustment over
  8.3 million matches. Global mapping (GLOMAP), fewer keypoints for the stream, or keyframes
  every 0.5 m would cut it.
- The rebuilt poses cost 2.6 dB of PSNR against the phone's; the photo modes cost 0.2–1.3 dB.

**So far:** with learned matching, every capture mode reconstructs the trail in one piece,
placed on the map from GPS alone to about 1.5 m, with scale within 3%. SIFT reconstructs none of
them in one piece. Still to try, in order:

- a live tracker on the stream: DPV-SLAM (27–50 fps on an RTX 3090 in its paper), scored the
  same way
- global mapping and fewer stream keypoints, against the 23 minutes
- GPS as a pose prior in the bundle adjustment, against the drift
- MapAnything, the plan's feed-forward option

## One photo (`ingest/recon/single.py`, `eval/single_photo.py`)

Glasses take a photo now and then. One photo can be used in two ways, and both are measured on the
same scan, whose ARKit poses say where every frame really was. Eight photos are spread along the
walk, each the sharpest frame within 0.15 s of its place, at least 6 m from either end.

**A. Alone: the photo becomes 3D by itself.**

| Method | What it is | License |
| --- | --- | --- |
| `flat` | the photo itself, no 3D: the baseline | — |
| `lidar` | the phone's LiDAR depth, one splat per pixel: the reference glasses cannot have | — |
| `depth-pro` | Depth Pro: metric depth from the photo, given its focal length (glasses are calibrated) | Apple research licence: eval only |
| `dav2-metric` | Depth Anything V2 Metric Outdoor Large: metric depth from the photo | Apache-2.0 per its model card |
| `sharp` | SHARP: 3D Gaussians from the photo in one pass | Apple research licence: eval only |

- Each depth makes one splat per pixel (at 640×480), sized to the pixel's footprint.
- SHARP's Gaussians are drawn as opaque discs one sigma across each way, and only those at least
  30% opaque. That approximates its own blending; `gsplat` would need building on Windows.
- **Scoring:** each is rendered where the phone really was 0.25, 0.5, 1, 2 and 4 m further along
  the walk, and compared with the frame the phone took there. At 0 m (its own pose) a lift must
  give back the photo: a check of the renderer and the conventions. Three numbers:
  - PSNR on the pixels the photo covers;
  - the flat photo's PSNR on the same pixels;
  - the share it covers. Pixels with no depth, such as the sky, count as uncovered.
- **Depth against the LiDAR:** the models' depth (and SHARP's Gaussians') is compared with the
  LiDAR's within 5.5 m, at medium or high confidence. Reported: median relative error, the same
  with the scale fixed (shape alone), median scale, and the share within 25%.
- **`+lidar-scale`:** each model again with its depth scaled to the LiDAR's for that photo. That
  is its shape alone; glasses could take the scale from GPS or from a scan of the place.

**B. Into the scan: where the photo was taken.**

- **The scan:** the same walk's frames every 0.5 m, with ARKit's poses and LiDAR depth. Frames
  within 1 s of the photo are left out, so the nearest is about 1.3 m away.
- **The prior:**
  1. The scan's path is fitted to the day's GPS (`align.georeference`: clock offset and a 2D
     similarity).
  2. The fix at the photo's time is carried back through that fit.
  3. The 12 scan frames nearest it, within 20 m, are matched with the photo (ALIKED +
     LightGlue).
- **The pose:**
  - the scan's depth makes the matches 3D;
  - PnP (COLMAP's LO-RANSAC, then refined) gives the photo's pose, three ways: all points with
    COLMAP's 12 px threshold, all points with 4 px (the one reported first), and only points
    from the LiDAR's reliable depth with 4 px;
  - the pose is scored against ARKit: metres, degrees, and the share within 0.25 m and 2°,
    0.5 m and 5°, 1 m and 10°.
- **What the scan shows from there:** the nearest three scan frames' depth is drawn at the pose
  found and at the true pose. Each is compared with the photo, and `vault/derived/single-photo/.../locate/`
  keeps photo | scan from there | difference.

**Running it:** double-click `check-single-photo.cmd`. The first run downloads the models, about
6 GB. The report is `vault/logs/single-photo.json`, and the renders stay under
`vault/derived/single-photo/`.

**On the synthetic walk** (`tests/test_single.py`, true depth as the LiDAR):

- **LiDAR lift:** 24.7–25.1 dB on 74–91% of the frame from 0.25 to 4 m ahead, against 12.4–15.1 dB
  for the flat photo on the same pixels. At its own pose it gives back the photo exactly.
- **A depth model 30% too deep:** measured as scale 1.30 (shape error 0.1%), with none of its
  pixels within 25%. It renders 16.7 dB at 1 m, and 24.1 dB with its scale fixed.
- **The self-check found a renderer bug.** A 1 px footprint rounded up to a 3×3 square, which
  blurred every render: 28.9 dB at the photo's own pose, now exact.
- **Locating:** all photos within 0.25 m and 2°; median 4 mm and 0.02°. The GPS fit found the
  3 s clock offset.

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

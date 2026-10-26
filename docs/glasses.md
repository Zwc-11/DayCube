# Glasses walks, simulated from a phone scan

Meta's glasses see but do not track: the Wearables Device Access Toolkit gives photos (4032×3024),
a 720p stream and the IMU's orientation, but no position. The plan
(`claude/glasses-3d-time-machine.md`, Phase 2) rebuilds a glasses walk from its images alone,
pins it to the iPhone's GPS, and trains it into splats.

Before any glasses or app, `eval/glasses_sim.py` measures how well that works on a walk whose true
camera path is known: a Stray Scanner scan, where ARKit gives the phone's pose for every frame.

## What it does (`ingest/recon`)

**1. Cut the video as glasses would take it** (`simulate.MODES`):

| Mode | Frames | Size |
| --- | --- | --- |
| `photo_3s` | a photo every 3 s | the video's (1920×1440) |
| `photo_1s` | a photo every second | 1920×1440 |
| `stream_5fps` | the live stream, 5 frames a second | 1280×960 (720p's size) |

**2. Rebuild from the images alone** (`sfm.py`, COLMAP 4.2 through pycolmap, BSD):

- SIFT features in every image, one shared pinhole camera.
- Each image matched with the next 10 (and, quadratically, a few further on).
- Incremental mapping; `--mapper global` uses GLOMAP. The largest model is kept.
- pycolmap installs from pip on Windows and Linux and runs on the CPU.

**3. Score against the phone's path** (`align.py`):

- A similarity fit (Umeyama) from the rebuilt camera centres to ARKit's.
- Reported: the share of images placed, the path error (RMS, 90th percentile, largest; metres),
  and the cameras' turn error (degrees).

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

Both are scored on the same 24 held-out frames that no photo mode trains on. Brush reads them from
`transforms_val.json`. Each frame is given its true pose, carried into each reconstruction's frame
by the fit. The difference (`psnr_cost_db`) is what the rebuilt poses cost.

## Running it

On the PC, double-click `check-glasses.cmd`. It runs:

    uv run --project services/capture-win python eval/glasses_sim.py --train

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
- images alone, a photo a second and a 0.4 s stream:
  - ≥ 90% placed
  - path error under 10 cm and turn error under 1°
  - the 3 s clock offset found
  - GPS residual under 1 m and scale within 4%
- the training sets, with a stand-in for Brush:
  - in metres both ways
  - each held-out camera where it stands among the training cameras in both
  - the rebuilt cameras upright

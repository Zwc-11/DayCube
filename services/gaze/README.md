# Eye tracker

Webcam gaze that follows head movement. MediaPipe face landmarks (the model EyeTrax uses) give,
every frame, the head's pose (perspective-n-point on MediaPipe's canonical face) and where each
iris sits on its eyeball; calibration learns each eye's direction within the head, and geometry
carries it to the screen (`gaze/head.py`). Then a One Euro filter and fixations (I-DT, with a
dispersion threshold that rises with the measured gaze noise), sent to the capture receiver.
Frames are processed in memory and never stored. Its own environment, because
[EyeTrax](https://github.com/ck-zhang/eyetrax) 0.4.0 (MIT; calibration screens, the camera, and
the older ridge models) pins numpy<2.

```powershell
uv sync --project services/gaze
uv run --project services/gaze python -m gaze check       # frame rate and face detection
uv run --project services/gaze python -m gaze calibrate   # 9 + 4 held-out targets, ~30 s
uv run --project services/gaze python -m gaze track       # normally started by the receiver
```

Calibration writes `vault/calibration/gaze.json` and `gaze-head.json`; tracking refuses a median
held-out error above 120 px at 1920 px wide (scaled to the screen width; 160 px at 2560). Keep the
head still while calibrating; afterwards it may move. `--model ridge` (or another EyeTrax model)
calibrates the old way, which is right only while the head stays where it was; a calibration made
before the head-aware model keeps working until you recalibrate, and `capture doctor` says so.

Why and how well: on MPIIFaceGaze (15 people, their own laptops, over weeks), calibrated on one
sitting and tested on the other days, median error is 43 mm on screen against EyeTrax's 78 mm,
and grows from 31 to 50 mm as the head moves 0-2 cm to 8+ cm from where it was, where EyeTrax goes
from 56 to 88 mm ([docs/capture.md](../../docs/capture.md#when-your-head-moves),
`eval/head_bench.py`).

The webcam is released while recording is paused, the session is locked, another app uses the
camera, or you are away.

Tests: `cd services/gaze; uv run pytest` (the head-aware model on synthetic faces posed in front
of a camera, fixations, smoothing, calibration fitting, the tracking loop with a scripted camera,
the receiver client).

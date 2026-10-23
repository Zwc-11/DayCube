# Eye tracker

Webcam gaze with [EyeTrax](https://github.com/ck-zhang/eyetrax) 0.4.0 (MIT; MediaPipe face
landmarks and a ridge regression from eye features to the screen), smoothed with a One Euro
filter and grouped into fixations (I-DT), sent once a second to the capture receiver. Frames are
processed in memory and never stored. Its own environment, because EyeTrax pins numpy<2.

```powershell
uv sync --project services/gaze
uv run --project services/gaze python -m gaze check       # frame rate and face detection
uv run --project services/gaze python -m gaze calibrate   # 9 + 4 held-out targets, ~30 s
uv run --project services/gaze python -m gaze track       # normally started by the receiver
```

Calibration writes `vault/calibration/gaze.json` and `gaze-model.pkl`; tracking refuses a
median held-out error above 150 px (scaled to the screen width). The webcam is released while
recording is paused, the session is locked, another app uses the camera, or you are away. See
[docs/capture.md](../../docs/capture.md).

Tests: `cd services/gaze; uv run pytest` (fixations, smoothing, calibration fitting with
EyeTrax's models, the tracking loop with a scripted camera, the receiver client).

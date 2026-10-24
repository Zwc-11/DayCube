# Eye tracker

Webcam gaze that follows head movement. MediaPipe face landmarks (the model EyeTrax uses) give,
every frame, the head's pose (perspective-n-point on MediaPipe's canonical face) and where each
iris sits on its eyeball; calibration learns each eye's direction within the head, and geometry
carries it to the screen (`gaze/head.py`). Then a One Euro filter and fixations (I-DT, with a
dispersion threshold that rises with the measured gaze noise), sent to the capture receiver; the
ring and the live point get their own filter (`gaze/stabilize.py`: a fixation estimate that holds
still, ignores single bad frames and jumps on confirmed saccades: half the jitter of One Euro,
[docs/capture.md](../../docs/capture.md#a-steady-ring), `eval/ring_filter.py`), and sits on the line
being read while the receiver's decoder is sure of it. A slow slide of the gaze during long looks
(a drooping lid) is measured with what moved with it (`gaze/drift.py`, reported by `doctor`;
[docs/capture.md](../../docs/capture.md#when-the-ring-slides-down)).
Frames are processed in memory and never stored. Its own environment, because
[EyeTrax](https://github.com/ck-zhang/eyetrax) 0.4.0 (MIT; calibration screens, the camera, and
the older ridge models) pins numpy<2.

```powershell
uv sync --project services/gaze
uv run --project services/gaze python -m gaze check       # frame rate and face detection
uv run --project services/gaze python -m gaze calibrate   # 9 + 4 held-out dots, 4 more after moving the head, ~45 s
uv run --project services/gaze python -m gaze track       # normally started by the receiver
uv run --project services/gaze python -m gaze.overlay     # the gaze ring over every window, without a camera
uv run --project services/gaze python -m gaze tune        # choose learning's settings on your own clicks
```

Calibration first looks for a 60 fps webcam mode (`gaze/camera.py`: MJPG at 1280 x 720 or 640 x
480, timed rather than trusted; `--fps 30` skips it) and saves it; tracking opens that mode. With
the ring switched on, the tracker draws it over every window itself (`gaze/overlay.py`, Windows):
a click-through, always-on-top window moved once per display frame between gaze points, left out
of screenshots ([docs/capture.md](../../docs/capture.md#the-gaze-ring-over-every-window)).

Calibration writes `vault/calibration/gaze.json`, the model (`gaze-head.json`, or EyeTrax's
`gaze-model.pkl`) and `gaze-frames.npz` (every frame's landmarks, no images). It fits three
models on the nine training dots, scores them on the held-out dots with the head still and moved
(after the tracker's smoother), prints the scores, and keeps the best: the head-aware model on
image-mode or video-mode MediaPipe landmarks, or EyeTrax's ridge (`--model` forces one). Tracking
refuses a median still-head error above 120 px at 1920 px wide (scaled to the screen width; 160
px at 2560).

Why and how well: on MPIIFaceGaze (15 people, their own laptops, over weeks), calibrated on one
sitting and tested on the other days, median error is 43 mm on screen against EyeTrax's 78 mm,
and grows from 31 to 50 mm as the head moves 0-2 cm to 8+ cm from where it was, where EyeTrax goes
from 56 to 88 mm ([docs/capture.md](../../docs/capture.md#when-your-head-moves),
`eval/head_bench.py`).

It keeps learning from clicks after calibration (`gaze/learn.py`): each click in any app, paired
with the steady look just before it, is a calibration point; the eye regression is refitted toward
them every 20 clicks and the change is kept only if it is more accurate on clicks it has not seen
([docs/capture.md](../../docs/capture.md#learning-from-clicks), `eval/click_learning.py`).
Lines you read are labels too: the receiver confirms a line from reading order (read between the
line above and the line below, the decoder sure, the page's position measured) and names the
fixations on it; the tracker kept their frames and learns the line's height from them, at a quarter
of a click, with clicks alone judging every fit ([docs/capture.md](../../docs/capture.md#reading-as-labels)).
`gaze/tune.py` replays your own clicks and lines from the calibration with other settings, chooses
on the older 70% and keeps a choice only if it is 3% better on the newest 30%; the tracker runs it
once a day at idle priority ([docs/capture.md](../../docs/capture.md#tuned-to-you)).
Each click is measured before it is learned from, by the model in use and by the calibration
alone: their ratio over the last 300 clicks is the live loss ratio (in the tracker's report, in
`doctor` and in the viewer's **Your data > Eye tracking**), and each update is logged when it is
fitted and when the first frame used it. Clicks are kept in `vault/calibration/clicks.jsonl` as
numbers (head pose, eye measurements, the pixel clicked, both errors, the model version), lines read
in `lines.jsonl`, fits, updates and tunings in `learning.jsonl`, the tuned settings in
`gaze-tuned.json`, and what was learned in `gaze-learned.json`, for the current
calibration only.

The webcam is released while recording is paused, the session is locked, another app uses the
camera, or you are away.

Calibration uses the latest-frame reader too, so slow dual-landmarker/UI work does not build an
ever-older FIFO of target observations when the backend supports that reader. Gaze gaps longer than
250 ms restart smoothing; shorter blinks retain its normal response. Looking off-screen ends an
open fixation. RGB conversion scratch is reused and released with the camera, while each MediaPipe
image owns its pixels.
The [tracking review](../../docs/gaze-tracking-review-2026-10-24.md) records measurements and limits.

Blink gating checks each eye, excludes closing/closed observations from its learned reference,
and waits 100 ms after the last rejected blink frame for reopening landmarks to settle. Brief
blinks preserve filter position without emitting held gaze samples; long closures reacquire
afresh. The [blink review](../../docs/blink-guard-2026-10-24.md) records replay results and tradeoffs.
The open-eye reference follows the iris's height in the eye, because the upper lid follows the
eye down: with one fixed reference, looking at the bottom of the screen read as a blink and the
gaze stopped around the middle ([below](../../docs/blink-guard-2026-10-24.md#looking-down-is-not-blinking)).

Tests: `cd services/gaze; uv run pytest` (the head-aware model on synthetic faces posed in front
of a camera, fixations, smoothing, calibration fitting, the tracking loop with a scripted camera,
the receiver client, camera modes, the ring's picture, interpolation and frame loop). The ring's
Windows window was run in Wine 9 on a virtual display.

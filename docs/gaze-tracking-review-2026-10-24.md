# Camera and gaze follow-up, 2026-10-24

The later [blink guard](blink-guard-2026-10-24.md) changes blink decisions and reopening behavior.
Strict full-extraction output comparisons below apply to their pinned measured sources.

This pass covers prediction cost, frame freshness during calibration and recovery when gaze
continuity breaks. Head-pose compensation, neural landmark inference, the regression/calibration
format, camera resolution and the 30 FPS target retain their existing behavior. The changes use
the preparation, batching and resource-ownership principles in the [DeepInfer review](deepinfer-review.md).

## CPU-stage measurements

Baseline: `abb6f48d96af2ff981e94329bd46b432d98542bb`. Reports record that base revision and
SHA-256 hashes of the actual sources tested:
[before](../eval/results/gaze-pipeline-before.json), [after](../eval/results/gaze-pipeline-after.json).
The measured implementation is committed as `bf2d483`. A subsequent cleanup adds release hooks
for RGB scratch when the camera is released; it changes no per-frame prediction/conversion math
and was checked with the complete 74-test gaze suite. These timings were not rerun for that cleanup.
The harness and its synthetic fixture source are identical in both reports. Windows, Ryzen 7
9800X3D, Python 3.11.9, NumPy 1.26.4, OpenCV 4.11.0 (16 threads), MediaPipe 1.1.1; 2,000 frames
per call, one warmup and eleven samples, seed 7. cProfile and tracemalloc run separately.

| Synthetic CPU stage | Median before → after, per frame | Sample p95 before → after, per 2,000 frames |
| --- | ---: | ---: |
| Head-model prediction | 35.88 → 32.36 µs | 74.62 → 66.89 ms |
| Head extraction plus prediction | 125.41 → 118.78 µs | 283.37 → 263.96 ms |
| 640×480 frame preparation | 78.61 → 75.65 µs | 175.30 → 179.10 ms |
| 1920×1080 frame preparation | 1,146.16 → 799.81 µs | 2,325.77 → 1,668.73 ms |

Prediction is about 10% faster and the combined head-math path about 5% faster here. Frame
preparation at 1080p is about 30% faster; process CPU for its 2,000-frame call fell from
11,625 to 7,546.88 ms. Parallel OpenCV work makes process CPU exceed elapsed time. At 640×480
the median difference is small and the sampled tail is slightly worse; this does not establish
a reliable tail-latency improvement for the 640×480 fixture.

Preparation runs the actual BGR→RGB conversion and MediaPipe `Image` copy, with a fixed synthetic
landmark result standing in for neural detection. Head extraction starts with projected numeric
landmarks and includes pose solving, eye geometry, packing, blink checks and prediction; it
excludes neural detection, camera/exposure/driver wait, smoothing, networking and rendering.
Consequently these gains are not a measured live FPS or end-to-end latency increase.

All four workload output hashes match exactly, including prepared RGB pixels, landmarks,
features, blink decisions and predicted points. Separate tests compare batched eye directions
with independent per-eye calculations for zero/single/multiple frames, preserve inputs/output
ownership, and exercise one or both eye rays missing the screen.

`Landmarker` now owns one reusable RGB conversion array, replaced on shape/dtype change and
released on close. Its pixel storage is 921,600 bytes at 640×480 or 6,220,800 at 1080p per
landmarker, plus array overhead. It remains allocated between frames and is released when the
camera stops or the landmarker closes. The release hook retains the neural detector, so resume
can allocate fresh scratch without reloading it. Each native MediaPipe
image still owns its copy: image/video tests verify a later conversion cannot mutate an earlier
image or returned landmarks. The benchmark's warm tracemalloc peak **does not charge this
already-allocated RGB scratch**, and misses some native allocations; it cannot establish total
memory savings or RSS. Calibration has two landmarkers, each with its own scratch.

## Freshness and recovery

Calibration previously read the camera directly while running two landmarkers and drawing the
targets. If that work falls behind a buffered driver, old eye positions can be paired with the
current target. It now uses the same Windows-aware latest-frame reader/fallback as tracking.
Only the newest pending frame is retained, superseded frames are skipped, and the camera still
opens/reads/releases on its owning thread. A cancellation regression checks the calibration path
uses that reader and releases the camera/landmarkers/windows. This is a plausible source of
calibration error; it has not been established as the cause of a particular person's failed fit.

The [freshness probe](../eval/results/camera-freshness.json) uses a synthetic 30 FPS source and
60 ms of consumer work, for three seconds per mode. Each frame contains only an index and a
monotonic timestamp. Direct FIFO consumption: median age 656.5 ms, p95 1,265 ms. Production
latest-frame consumption: median 16 ms, p95 32 ms, maximum 47 ms. Both processed 50 frames;
latest-frame mode skipped 39 superseded frames. This is one overload demonstration, including
OS scheduling; it is not a real-camera age measurement or a production percentile guarantee.

The tracker also now resets smoothing after a gaze gap longer than the fixation detector's
existing 250 ms continuity limit. Reacquiring a face starts at the new measured point rather
than dragging the old location into it. In the deterministic regression the previous first
point lagged the new synthetic target by about 69 horizontal pixels; the reset removes that
specific carry-over. The first returning sample is unfiltered, as at startup; subsequent frames
resume normal smoothing. Short blinks keep the original continuous response. A filtered point
outside the permitted screen margin now flushes the open fixation immediately. Its end remains
the last accepted sample time, so off-screen time is not added to it. No location is fabricated.

The [regression simulation](../eval/results/gaze-tracking-regression.json) compares against the
baseline on seeds 1000–1009: ten scenarios, three synthetic error profiles, two Windows scaling
factors, 600 sessions per revision. All 300 no-dwell cases pass in both revisions. Target dwell
passes are mixed: one coarse-calibration case falls from 6/10 to 5/10, while one recalibrated
re-reading case rises from 5/10 to 6/10. Line/recording metrics also shift in both directions.
These small samples do not establish a general accuracy increase. No filter parameters were
tuned on these seeds. A trial of grouped-target regression validation on local calibration
numbers did not produce a usable fit, so regression training was left unchanged.

## Validation and live limits

The final `npm run check` passed: Ruff, canonical schema consistency, 4 contract tests, 9 transport
tests, 229 core tests, 74 gaze tests, 10 extension unit tests, TypeScript and the production build.
Both isolated API browser tests also passed. The live process was not interrupted to apply the
final camera-release cleanup; it takes effect on its next normal restart.
No webcam was opened by these benchmarks/tests, and no images, calibration landmarks, tokens
or recordings are included in the committed fixtures or reports. All simulated pixels stay in RAM.

The user then ran calibration and the supervised tracker restarted. The
[aggregate live check](../eval/results/gaze-live-2026-10-24.json) has 15 active status samples:
median 29.3 FPS, range 28.9–29.6, and 100% face detection throughout. FPS/face samples share a
rolling window. This confirms operation at the existing 30 FPS target, not a faster camera mode.
The new calibration reported 37.2 px still-head error against a 160 px gate, but 277.5 px after
head movement. These scores are measured before the selected model is refitted on all dots;
they are not an independent test of that final refit. Prior sessions and head movements differ,
so the calibration improvement cannot be attributed entirely to this code change. Tracking is
usable with the head still; real head-motion accuracy and steady-target jitter need further
held-out device checks. Local geometry/regularization probes did not justify another model change.

The tracker retains its existing usability gate. The latest-frame reader still has a synchronous
fallback for cameras that cannot run on its thread, and blocking native camera calls can outlive
the join timeout. Genuine head-motion accuracy, exposure latency, camera availability during calls,
lock/unlock behavior, and long-session CPU/RSS need device acceptance testing.

Repeat from the repository root:

```powershell
uv run --project services/gaze python eval/benchmark_gaze_pipeline.py --output test-results/gaze-pipeline.json
uv run --project services/gaze python eval/profile_camera_freshness.py --output test-results/camera-freshness.json
uv run python eval/gaze_sim.py --seeds 10 --workers 4 --compare abb6f48 --output test-results/gaze-regression.json
uv --directory services/gaze run pytest
```

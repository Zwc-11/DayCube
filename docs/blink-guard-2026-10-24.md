# Blink artifact guard, 2026-10-24

The gaze circle could dip while the eyelids closed or reopened, then return. A synthetic replay
reproduced three corrupted live updates during a short blink and 42 updates during a longer eye
closure. The previous detector averaged both eyes, learned its reference from closed frames too,
and let partially reopened frames reach the model. The smoother also treated time spent blinking
as a long filter step, making the first returning sample unusually influential.

## Change

- Check each eye's lid-height/eye-width ratio separately. Reject either eye below 80% of its
  established open reference, or a fall of more than 10% since its previous observation.
- Keep at most 50 accepted aperture pairs and running totals. Closed/closing frames do not lower
  an established reference. A short bootstrap supports naturally narrow eyes; clearly closed
  or nonfinite/degenerate observations stay suppressed.
- After the last rejected blink frame, withhold predictions for 100 ms using the monotonic clock.
  This excludes the early reopening frames where the iris landmarks still follow the lid.
- For a blink/recovery shorter than 750 ms, preserve the last trusted filter value, clear its
  derivative and resume with one nominal frame interval. Gaze timestamps remain actual wall-clock
  timestamps. A longer closure starts smoothing afresh. Releasing the camera clears the guard.

The tracker sends no new live positions or fixation samples during the guard; it does not turn
a held display location into recorded gaze. The existing browser ring can retain the last trusted
point briefly and fade when points stay unavailable. Regression coefficients, calibration files,
camera settings, UI design and evidence contracts are unchanged. A recalibration is not required.

## Evidence

[Replay report](../eval/results/blink-replay.json): baseline
`2cd4811` (resolved full revision in the report), the same fixtures/interpreter for both versions,
and hashes of all compared sources plus the common harness/tests. All pixels/points are synthetic;
no camera, neural inference, HTTP or recording is used.

| Scripted 30 FPS case | Before | After |
| --- | ---: | ---: |
| Corrupted updates during closing/reopening | 3 | 0 |
| Maximum blink-only vertical artifact | 369.12 px | 0 px |
| Updates during three seconds of closed eyes | 42 | 0 |
| Healthy downward saccade: within 50 px of target | 66.67 ms | 66.67 ms |

The complete healthy saccade trajectory hashes match. This verifies the supplied unobscured-eye
fixture, not every person's blink/eye-motion separation. Tests also cover either eye closing,
long closure without baseline contamination, initially closed/narrow eyes, invalid landmarks,
gentle aperture changes, 15/30 FPS reopening timing, filter stability and pause/release cleanup.

Separate unpaced classifier timing, seven samples of 50,000 calls after a warmup on this Windows
Ryzen 7 9800X3D / Python 3.11.9 / NumPy 1.26.4: median 3.25 → 4.17 µs per call. The new checks
cost about 0.92 µs here; this is not a speedup or an end-to-end latency measurement. cProfile and
tracemalloc are separate runs. History stays capped at 50 entries. Temporary Python allocations
are included in traced peaks; native/RSS memory is not measured by this workload.

The [dwell/reading regression](../eval/results/blink-regression.json) compares 600 synthetic
sessions per revision: ten scenarios × three error profiles × two scaling factors × seeds
1000–1009. All 300 no-dwell sessions pass in both versions. The intentional missing samples
reduce median fixation coverage across scenario cells by 2.28 percentage points (range
0.53–4.12 points). Outcomes are mixed: two coarse stare cases improve by one trial each;
look-away dwell cases lose one or two trials, and one recalibrated re-reading case changes
from 6/10 to 4/10. These tradeoffs are retained in the report, not replaced with synthetic gaze.
Ten seeds per cell are not a population accuracy estimate; parameters were not tuned on these seeds.

`npm run check` passed: Ruff, schema consistency, 4 contract tests, 9 transport tests, 229 core
tests, 85 gaze tests, 10 extension library tests, TypeScript and the production build. The eleven
new blink regressions include seven failures reproduced on the original code before the fix.
All five isolated reporter browser tests also passed.
The full extension/receiver suite cannot share port 7700 with live capture; it was not used to
interrupt that service.

The existing supervised gaze worker was reloaded to apply the fix. Receiver pause settings and
calibration contents were not changed. The updated worker reported tracking at approximately
29 FPS with a face detected; live-update counts remained nonzero. The user reported that the circle
stayed at the original position until real eye movement resumed, consistent with the intended hold.
That is a qualitative check, not a labeled video or measured blink sensitivity/specificity.

## Integration with newer branch work

The original replay/simulation above are pinned to the blink implementation in `7f241d5`.
Newer branch work added a desktop overlay/camera-mode path and a search freshness fix, followed
by an overlay frame-counter race fix. Those changes were preserved through normal merges.
The blink gate stays before `_gaze`, so it also guards the desktop overlay's in-memory `push`.
A new regression checks that neither closing nor reopening artifacts reach that overlay.
The calibration lifecycle fixture now replaces the new camera wrapper and explicitly forbids
real mode probing; no test opens the user's webcam.

The [integrated replay](../eval/results/blink-replay-integrated.json) and
[integrated simulation](../eval/results/blink-regression-integrated.json) record the merged
sources separately. The replay verifies sources/fixtures stayed unchanged while it ran; the
simulation wrapper does the same. Earlier evidence is retained with its original hashes.
The final merged `npm run check` passed with 233 core tests, 100 gaze tests, 4 contract tests,
9 transport tests, 10 extension library tests, schema/type checks and the production build.
All five isolated reporter browser tests also passed after the merge. Native Win32 drawing
was not manually revalidated by this blink pass; its integration was tested through fake backends.

## Limits and reproduction

The 100 ms reopening grace is an intentional responsiveness tradeoff around blinks. An aperture
drop can also come from a squint or a large pose/downward-gaze change; eye ratios alone cannot
perfectly distinguish those cases. Real sensitivity, slow partial blinks, glasses and different
lighting still need device checks. The earlier head-motion accuracy limit is not solved here.
Earlier pipeline performance reports stay pinned to their measured sources; blink-decision hashes
may change intentionally in a newer full-extraction benchmark.

```powershell
uv run --project services/gaze python eval/blink_replay.py --compare 2cd4811 --output test-results/blink-replay.json
uv run python eval/gaze_sim.py --seeds 10 --workers 4 --compare 2cd4811 --output test-results/blink-regression.json
uv --directory services/gaze run pytest tests/test_blinks.py
```

## Looking down is not blinking

Reported on the PC after this guard: "when the eyes go to the lower half of the screen, the gaze
sometimes gets stuck in the middle". The upper lid follows the eye: looking down lowers it. On
MPIIFaceGaze (15 people, webcam images of open eyes looking at known points), the lid gap was 0.99
of its top-of-screen size in the screen's top quarter, 0.94, 0.88 and 0.83 further down. A fixed
open reference, which closed frames never lower, called the bottom of the screen a blink: the
frames were dropped, the point stayed where it was (about the middle), and the reference never
caught up because the dropped frames could not teach it. The guard's replay missed it because its
downward-saccade fixture kept the lids open.

Change (`gaze/head.py`, `Blinks`):

- The open reference follows the iris: each eye's aperture is compared with the aperture expected
  at its iris's height in the eye (the iris's distance below the line between the corners, over
  the eye width), a least-squares line through the last 900 open frames, re-levelled by the last 50.
  The slope is never positive and at most 3.5 (MPIIFaceGaze people: 0.3 to 1.8).
- Eyes shut (under half of the open average) are always a blink, whatever the eye's direction.
- A narrowing that lasts 0.3 s since the lids were last shut, without shutting, is a look down or a
  squint, not a blink: the frames are used and learned from, so the reference catches up.
- A frame-to-frame fall counts as closing only beyond what the iris's own move explains, so a
  downward saccade, which lowers the lids with the eye, no longer holds the point for 100 ms.

Evidence:

| | top quarter | 2nd | 3rd | bottom quarter |
| --- | ---: | ---: | ---: | ---: |
| open-eye MPIIFaceGaze frames called closed, fixed reference | 0.8% | 1.7% | 6.0% | 16.8% |
| the same, reference following the iris | 0.9% | 1.5% | 4.3% | 10.3% |

(`eval/blink_downgaze.py`, `eval/results/blink-downgaze.json`: each person's images through
`Blinks` in order, the long-narrowing rule off because these are single images seconds apart, so
only the reference is measured. On a video the long-narrowing rule then uses whatever remains after
at most 0.3 s.) A larger monitor than these laptops' asks more downward gaze, so the fixed
reference did worse there. `tests/test_blinks.py` reproduces the report end to end: after reading
the upper screen, a look at the bottom of a large monitor (lid gap 72% of the average) gave **no**
live point for 0.5 s with the fixed reference, and reaches the bottom with this one. This guard's
replay (`eval/blink_replay.py`) is unchanged: 0 corrupted updates in a partial blink, 0 px blink
artifact, 0 updates in three seconds of closed eyes, the healthy downward saccade arrives in
67 ms. The classifier costs 18 us a frame here (7 us before), of a 33 ms frame.

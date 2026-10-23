# Windows capture acceptance check

Workstation check on 2026-10-23, branch `capture-browser-gaze`. This is an in-progress
acceptance record. Only aggregate counts and calibration/status metrics are retained here;
no vault contents, page text, tokens, images or fixation records are included.

| Step | Result | Evidence and remaining work |
| --- | --- | --- |
| 3: camera check | Pass | 26.1 FPS, 100% face detection before the rate change. Updated live tracker: 29.3 FPS median at a 30 FPS target, 100% face detection in the measured sample. |
| 4: calibration | Pass for the configured gate | Median held-out error 182.9 px, p90 482.0 px, `usable=true`, 2560×1440 monitor. These metrics do not establish paragraph accuracy. |
| 5: autostart installation and live services | Pass for installation/current run | Receiver and supervised tracker running; both logs have zero ERROR-level entries. A real sign-out/sign-in remains step 7f. |
| 6: Opera options | Pass | User confirmed Recording On, receiver running, eye tracker 29.7/30 FPS and 100% face detection, queue 0. |
| 7a: five-minute browsing | Pass | New browser records 49, text snapshots 25, gaze fixations 49; no malformed lines. Counts below. |
| 7b: paragraph dwell | Fail; live retest pending | No dwell. The intended public paragraph had 5 hits totaling 0.67 s, below its 12.61 s threshold. A focus snapshot bug was reproduced and fixed; its contribution to this failed stare is not established. |
| 7c: shortcut pause/resume | Pending | Requires webcam-light timing and a settled no-write interval. |
| 7d: lock/unlock | Pending | Requires a real Windows session lock. |
| 7e: video call | Pending | Requires a real call app to request the webcam. |
| 7f: sign-out/sign-in | Pending | Registry installation alone does not establish this behavior. |
| 7g: placement and geometry | Partial | During 7a, 6 fixations hit blocks; geometry was exact in 24 of 25 snapshots, estimated in 1. Paragraph placement still needs a successful reading test. |

## Browsing counts

The test interval was 299.6 seconds. Record deltas by kind:

| Kind | New records |
| --- | ---: |
| page | 10 |
| title | 10 |
| focus | 9 |
| idle | 2 |
| search | 3 |
| click | 3 |
| media | 8 |
| selection | 4 |
| blocks | 25 |
| gaze fixation | 49 |
| dwell | 0 |

The action checks found 3 Google searches, 4 YouTube play events, 2 link clicks and 4 copied
selections. This does not imply that every click record was a link click or every media
record was a play event.

## Fixes and validation

- Receiver packaging: include the ignored source package in wheel/sdist archives; verify the
  installed CLI and archive contents (`47e631d`).
- Processing target: default 30 FPS, monotonic pacing/rate measurement, owned prediction input
  reuse, separate measured/target telemetry, and honest paused status (`2d2efca`). The
  [performance report](performance.md#windows-gaze-processing-at-30-fps) records measurement limits.
- Reporter focus lifecycle: returning via focus or visibility now invalidates the shape-only
  deduplication key and sends fresh block positions. The receiver clears its front page on focus
  loss; a view heartbeat alone cannot restore those positions. Stable ids and the text cache
  remain intact. Two synthetic DOM/transport tests failed before this change and pass after it.
  They do not establish that the live paragraph test is fixed.

Current deterministic checks: 127 core tests, 30 gaze tests, 6 extension library tests and
2 reporter browser tests passed. The reporter tests connect to no receiver and use no camera
or user vault. The full extension/receiver e2e suite requires port 7700 free and was not run
against the user's live receiver.

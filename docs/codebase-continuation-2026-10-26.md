# Continued codebase review

Continued from `2450e86`. The earlier [performance pass](codebase-efficiency-2026-10-26.md)
retains its own results and unfinished coverage; this continuation reviews those remaining modules.
The same pinned DeepInfer reference supplies ownership, preparation/replay and batching principles.
This is not a reproduction of its accelerator results or a claim that every dependency is optimal.
The reference's main revision was checked again on October 26: still
`553741557a57df44a46fb3ab72b6aafeaf945337`. Files examined remain those named in the earlier report;
they were not all reread. The additional DAYCUBE review covers the source, tests and supporting
configuration previously missing current manual notes, with findings bound to file hashes in
[the ledger](review-ledger.json). Generated code, dependency locks, fixtures and assets have separate
treatment; this is a scoped implementation/resource review rather than an exhaustive dependency audit.

## Failed and late scene loaders

An isolated browser fixture exposed two ownership failures: MeshView registered cleanup only after
awaiting glTF, and SplatView left its loading animation running after both loads failed. A scene now
registers releases as it acquires resources. Releases run once in reverse order, including late
models arriving after close. Failure closes the renderer/controls/observer/loop; shared mesh geometry,
materials and textures are released once. Error UI remains explicit and the successful view's design
is retained. Cleanup errors are reported while the remaining releases continue.

| Synthetic scenario | Before | After |
| --- | --- | --- |
| Mesh load rejects | Renderer not disposed, including after unmount | One disposal |
| Mesh arrives after unmount | Renderer/late geometry not disposed | Renderer and geometry disposed once |
| Both splat loads reject | 18 further draws in a 300 ms sample | Zero draws after settling; one disposal |

These are actual renderer-method/geometry counters in headless Chromium, not FPS, RSS or hardware
GPU measurements. No recording/frame is read or saved. The baseline profiler/fixture source is
retained in commit `20c09d4`; the after profiler shares its driver with three browser regressions.
Reports: [before](../eval/results/scene-lifecycle-before-2026-10-26.json),
[after](../eval/results/scene-lifecycle-after-2026-10-26.json).
The after report was rerun after integrating the concurrent splat changes at `53b1e57`.
GPU timer queries and the long-task observer also belong to the scene's cleanup scope.

## Camera map playback

The filtered points array invalidated geometry preparation on every media update, and SVG path
strings were rebuilt on every render. The map now owns one prepared Float64 coordinate buffer and
one path string per camera input. Cursor lookup uses bisection for chronological poses and the
original first-match scan for unordered/invalid times. Its projection, heading, units, clamping,
duplicate-time policy and final SVG markup are unchanged. Extents no longer spread arrays into
function arguments, so long recordings do not exceed the JavaScript argument limit.

Actual React component, synthetic paths, 61 updates after five warmups, Chromium 156.0.8078.4,
Node 24.11.1, 800×600/device scale 1, Ryzen 7 9800X3D. Preparation and SVG paint are outside the
React update durations. Allocation sampling covers the whole page across those updates, including
collected objects; it is cumulative statistical allocation, not peak live memory.

| Points | Before median / p95 | After median / p95 | Before / after sampled allocation |
| --- | ---: | ---: | ---: |
| 600 | 0.20 / 0.40 ms | 0.10 / 0.20 ms | 14.24 / 0.71 MB |
| 12,000 | 2.60 / 3.80 ms | 0.10 / 0.20 ms | 276.11 / 0.79 MB |
| 50,000 | 9.10 / 13.80 ms | 0.10 / 0.20 ms | 1,134.02 / 0.77 MB |

Exact SVG hashes match at each scale. The baseline source is retained in `93764d2`. Unit cases
compare the original projection, cursor ties/order and a 150,000-point buffer. Reports:
[before](../eval/results/camera-map-before-2026-10-26.json),
[after](../eval/results/camera-map-after-2026-10-26.json).

## Long day adaptation and status accuracy

The real `fetchDay` path returned `offline` for 200,000 unpositioned path points because a spread
of their confidence values exceeded the browser's argument limit. A reduction preserves the
minimum and initial 0.4 cap without a temporary values array or argument expansion. The browser
regression fails before the change and passes after with confidence 0.125 and illustrative fidelity.
No transport latency improvement is claimed from this correctness case.

The disconnected viewer no longer says recording is stopped: it directs users to the extension's
authoritative status. Privacy text also reflects stored original phone media and unavailable face
blurring, rather than promising processing that is not implemented. No live capture state changed.

## Phone segmentation

Each span previously scanned and reconverted timestamps across the recording's fixes and steps;
each stay split repeated a scan of the span. Timestamp preparation now happens once per recording
and once per stay, with bisection for finite chronological data. Unordered and nonfinite data retain
the original scan and source order. Closed endpoints, including the sample shared by adjoining
windows, remain unchanged. The compact timestamp arrays belong to that segmentation call.

Synthetic 1 Hz fixes/steps, alternating stays/drives every 900 seconds, seven samples after one
warmup, Python 3.11.9 on this Windows/Ryzen 7 9800X3D PC. All span fields and original Fix fields,
including timestamp offsets and order, have identical before/after hashes. Timing includes span
summary construction; full output hashing is outside timing. The baseline script is in `96d20e9`.

| Hours | Before median / sampled p95 | After median / sampled p95 | Before / after traced peak |
| --- | ---: | ---: | ---: |
| 2 | 32.19 / 32.78 ms | 19.46 / 23.11 ms | 0.20 / 0.31 MB |
| 8 | 392.30 / 519.60 ms | 70.20 / 71.90 ms | 0.67 / 1.12 MB |
| 24 | 4,115.55 / 4,175.34 ms | 287.18 / 291.73 ms | 1.91 / 3.28 MB |

At 24 hours this is 14.3× less elapsed time on this workload, with about 1.37 MB more traced
allocation at the peak. An initial Python-list timestamp index used 5.33 MB there; compact doubles
reduced that cost while adding a little preparation time. These are tracemalloc measurements,
not process RSS, and seven observations do not estimate a production tail distribution. Long sparse
recordings still allocate per-second activity labels; joined overlapping recordings use the scan
fallback. Reports: [before](../eval/results/phone-segments-before-2026-10-26.json),
[after](../eval/results/phone-segments-after-2026-10-26.json).

## Voice gallery clustering

Average-linkage already updates only one similarity row/column per merge, but locating the next
merge rescanned the whole matrix. Large galleries now cache each row's first maximum. A merge
refreshes invalidated winners and compares the new column for the others. Row-major ties,
same-recording cannot-link constraints, merge arithmetic and final labels match the original.
Small galleries retain the original scan: the bookkeeping was slower at 128/512 vectors in the
crossover experiment. The cache starts at 1,024 vectors.

Seeded normalized 192-dimensional float32 vectors, four synthetic voices, two speakers per
recording, seven samples after one warmup, NumPy 2.4.6 and `OPENBLAS_NUM_THREADS=1`. The baseline
target module is extracted from `af95058`; the report hashes the module actually imported. Labels
match exactly in all four benchmark sizes and in 360 small randomized oracle cases plus tie cases.

| Vectors | Before median / sampled p95 | After median / sampled p95 |
| --- | ---: | ---: |
| 128 | 1.15 / 1.68 ms | 1.15 / 1.18 ms |
| 512 | 36.46 / 43.21 ms | 23.26 / 24.94 ms |
| 1,024 | 152.67 / 169.32 ms | 109.07 / 122.78 ms |
| 1,536 | 499.77 / 542.02 ms | 271.79 / 289.86 ms |

The 128/512 paths use the same algorithm, so their variation is not an optimization result. The
1,536-vector case is 1.84× faster; the dense similarity matrix still sets the traced peak of
28.31 MB in both runs. Storage remains quadratic and adversarial invalidation can still be cubic.
No recording, voiceprint gallery, model or GPU job was opened. Reports:
[before](../eval/results/voice-clusters-scale-before-2026-10-26.json),
[after](../eval/results/voice-clusters-scale-after-2026-10-26.json).

## Failure handling and bounded intake

Three local Python stand-ins reproduced trainer failures without installing Brush or running a GPU:
a silent trainer bypassed its deadline, a progress callback exception left its process alive, and
a long output line remained in the diagnostic tail. An independent watchdog now enforces the
deadline even during a blocked pipe read. Process/pipe/log ownership closes on success and failure;
partial lines and retained diagnostics are bounded. All three regressions fail before and pass
after. This owns the direct trainer process; it is not a generic process-tree cancellation facility.

Seven synthetic HTTP cases reproduced the gaze client discarding fixations on malformed or
mismatched HTTP 200 replies. It now requires nonnegative integer accepted/duplicate/stale counts
that account for the batch before dequeuing. Refused acknowledgements keep the exact record IDs
for retry. All 14 receiver transport tests pass. This changes no gaze filter, calibration or FPS cap.

Phone uploads previously read the entire body before checking the 4 MiB chunk limit. They now
stop reading as soon as that budget is exceeded, before storage is touched. Upload IDs must match
the generated lowercase 32-hex format before metadata path access. Five focused cases fail before
and pass after; the upload/phone/splat/trainer group passes all 22 tests with FFmpeg available.
ASGI server-owned incoming chunks and concurrent requests still contribute memory outside this
application buffer budget.

The reporter now resends line layout/text when a block's accepted text changes, even when its
length and rectangle do not. A real Chromium DOM regression reproduces the stale-line failure
and checks unchanged block identity/geometry with updated line text. All six reporter tests pass
against a synthetic transport. Edits beyond the clipped text key and style-only wrapping changes
remain gaps; this is not a complete DOM invalidation solution.

## Integration and provenance

Another PC session committed the working changes in `bba1ca8`/`734cf43`, including two preexisting
benchmark drafts. Their contents were preserved. Its concurrent splat work (`5541a4c`, `17580e9`,
`53b1e57`) was integrated with normal history: the GPU budget remains its own policy and scene
resource ownership still covers failure/late completion. This continuation did not run the
real-vault GPU profiler or verify that session's hardware measurements. Its policy tests are
included in the combined browser checks.
The later `502f004` update changes only the optional GPU profiler and its documentation to
separate settling from rest. It was read and syntax-checked after the combined gate, without
running its real-vault workload; production viewer and backend code are unchanged by that update.

Timing baselines and after reports retain their original source hashes and revision metadata.
The scene lifecycle after report is refreshed for the integrated source. Another process added
only npm `peer` metadata to the working lockfile; dependency versions, integrity hashes and the
entire lock data after removing those metadata flags match the tracked lock. That local edit is
preserved, and no speed change is attributed to it. The original capture-index draft/report is
historical evidence, not a new result of this continuation.

## Remaining limits

The per-file ledger records the detailed findings, including modules left unchanged after review.
These are priorities for follow-up, not claims that reading every file made all paths optimal:

- Viewer startup still loads large Three/Spark chunks (about 0.96/2.49 MB minified in this build).
  Production cold-start, real-GPU long sessions, texture CPU storage and 2D allocation traces need
  representative inputs. Camera-pose interpolation still makes small arrays while following.
- Full-history discovery and optional source/gallery rebuilds still scan history. Metadata/cache
  accounting budgets do not measure total process RSS. Cold concurrent indexing and external
  mutation/failure interleavings need broader testing.
- Capture intake has no transaction across several append logs: partial writes can duplicate records
  on retry, and attention state is not rolled back. Some startup/shutdown paths, hook writes,
  live-sender threads, native camera/FFmpeg calls and optional job stages lack complete cancellation.
- Phone reimport/publication spans several files without rollback. Upload completion retry and
  same-size changed-content acknowledgement need dedicated fixes. Device hashing under one lock
  can block other uploads.
- Time-bounded gaze/label buffers assume progressing timestamps; some retained entries are count
  bounded rather than byte bounded. Learner files are compacted on load, with whole-file startup
  reads; background refits/tuning repeat feature preparation and need cancellation/resource traces.
- Calibration constructors and partial native overlay construction need failure cleanup coverage.
  Desktop API signatures, camera-busy/lock behavior and physical webcam release require Windows
  acceptance. Simulator accuracy is not live-camera accuracy.
- The two real-extension/receiver browser suites require port 7700 free and need safer isolated
  startup/teardown. They were not run against the active receiver. Wine-only launcher tests skip
  on this PC; actual model canaries, paid/cloud jobs and user-recording benchmarks were not run.

## Coverage and validation

The ledger inventories 510 tracked files (excluding itself): 227 source, 83 test and 43 supporting
configuration files all have current hash-bound manual notes, 353 in total. The other 157 entries
are 11 generated outputs, five dependency locks, 17 assets, 19 fixtures, 72 measurement/fixture
files and 33 documents, with explicit treatment by category. No vendored implementation is tracked;
installed dependency internals are outside the manual audit. Ledger regeneration preserves the
notes byte-for-byte and verifies every recorded file hash. Existing earlier notes retain their
own scope; the new notes identify complete-file reads rather than structural checks alone.

Combined code based on `53b1e57`, with FFmpeg/ffprobe 9.0.2 available locally:

- `npm run check`: Ruff, schema regeneration consistency, four contract tests, nine transport
  tests, 355 backend tests (seven Wine-only skips), 147 gaze tests, ten extension library tests,
  eight viewer unit tests, 19 model-free speech tests (four model-dependent skips), TypeScript
  and production build passed. A Starlette/httpx deprecation warning remains.
- API browser suite: two passed. Combined viewer browser suite: 40 passed, including successful
  phone scenes, idle/modal stopping, failed/late loaders, long paths and adaptive splat policy.
  Extension reporter browser suite: six passed with a synthetic transport.
- Capture wheel/source-archive contents passed: 18 Python modules and no runtime data. The isolated
  capture environment's CLI help starts successfully. No receiver or tracker was restarted.
- All three scene lifecycle scenarios pass `--verify` after the concurrent splat integration.
  Exact SVG, full phone-span and voice-label fingerprints match their timing baselines.
- A before/after validation source manifest confirms production/test/dependency files stayed
  unchanged during the combined checks. Only `scripts/review-inventory.py` changed afterwards
  to add review-category metadata; its Ruff check and two identical ledger regenerations passed
  separately. All current benchmarked implementation hashes match; the camera-map report retains
  the earlier lockfile's equivalent dependency resolution before the nine `peer` metadata additions.

Identified gaps stay visible; current manual coverage is not proof of optimal algorithms,
full line-by-line dependency auditing or completed live-camera/GPU/model acceptance. Published
history from other sessions is preserved. New commits from this review use the configured Zwc-11
identity; other sessions' existing authorship is not rewritten.

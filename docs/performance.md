# Viewer rendering performance

The viewer uses demand-driven rendering. A paused diorama should finish camera easing and stop
drawing. OrbitControls invalidates frames while orbiting/zooming; playback requests animation
frames while it runs. The scene and timeline pause while a modal is open or the page is hidden.
The 2D fallback redraws for data/time/camera changes and resize, rather than running an idle loop.

The default render resolution is capped at one device pixel per CSS pixel. Directional shadows
use a cached 512px map refreshed for scene, light/time, cutaway and floor changes. The separate
contact-shadow pass was removed. Large backdrop blurs were replaced with opaque panels; tilt-shift
and mouse parallax remain opt-in. Hover motion values avoid React updates on every pointer move.
Scene labels and pins use fixed pixel sizing rather than a distance factor that magnified
their DOM surface hundreds of times on the orthographic camera. The FPS readout no longer clamps low measurements to 30.

## Measured idle sample

Same 1440×900 page, device scale factor 1.5, four seconds idle after settling, headless Chromium
using its SwiftShader software renderer:

| Measurement | Before | After demand rendering |
| --- | ---: | ---: |
| GPU draw calls in sample | 1,624 | 0 |
| Main-thread task time | 4,143 ms | 1 ms |
| Main-thread tasks longer than 50 ms | 55 | 0 |
| WebGL backing resolution | 2160×1350 | 1440×900 |

These numbers describe this idle benchmark, not a promised interactive frame rate on every GPU.
The final label-size fix additionally removes the oversized composited surface.

With the local dev server running, collect a current sample with:

```powershell
node scripts/profile-viewer.mjs current
```

The report is saved to `test-results/perf-current.json`. Regression checks run with:

```powershell
npm run test:e2e:viewer
```

Those checks verify that idle WebGL/Canvas2D draws stop, orbiting and timeline scrubbing redraw,
playback renders, modal background rendering stops, scene labels stay label-sized, and API
token binding/search/reindex still work. GPU tests run one browser at a time.

Implementation references: [React Three Fiber demand rendering](https://r3f.docs.pmnd.rs/advanced/scaling-performance)
and the installed drei OrbitControls implementation, which invalidates on control changes.

## Local service scale benchmark

The DeepInfer-inspired backend pass groups events/fixes once per day, uses a binary-search
timeline for nearest OCR evidence, and batches SQLite inserts with a single FTS rebuild.
Nearest-event matching preserves the ten-minute cutoff, earlier-time ties and first record
at duplicate timestamps. Tests also check that a failed rebuild preserves the previous index.

Same Windows workstation, Python 3.11.9, SQLite 3.45.1, one warmup and five timed repetitions
per operation, warm filesystem, isolated temporary synthetic vaults. Fixture creation, result
hashing and verification are outside the timing region. `build_index` includes validation,
evidence collection, database creation and atomic replacement; it is not just SQL insertion time.

Large workload: 50 visits, 5,000 events, 10,000 path fixes, 1,000 screenshots with three OCR
blocks each (8,050 indexed units). Medians:

| Operation | Before | After |
| --- | ---: | ---: |
| Assemble viewer day | 99.75 ms | 66.21 ms |
| Collect index evidence | 4,540.19 ms | 42.22 ms |
| Full index rebuild | 6,081.41 ms | 1,920.35 ms |
| Warm retrieval | 1.74 ms | 1.97 ms |

Warm retrieval was not optimized; sub-millisecond variation is not evidence of an improvement.
The small workload (5 visits, 250 events, 500 fixes, 50 screenshots) and all raw samples,
runtime metadata, revisions and source hashes are retained in
[before](../eval/results/core-before.json) and [after](../eval/results/core-after.json) reports.
Day, collected evidence, build-summary and retrieval output hashes match across versions.
The current run also checks every stored evidence row and FTS external-content integrity.
Five samples are useful for a local comparison; their nearest-rank p95 is the maximum sample,
not a reliable estimate of production tail latency.

```powershell
npm run bench:core -- --output test-results/core-current.json --compare eval/results/core-before.json
```

The benchmark never reads the user's vault, invokes cloud services or runs capture. It fails on
result drift and only writes a completed report after all selected workloads succeed. These are
CPU/storage measurements for the named synthetic workloads; they do not measure browser frame
rate, HTTP latency or DeepInfer throughput. The [reference review](deepinfer-review.md) and
[engineering standard](engineering.md) describe the applicable patterns and remaining work.

## Second pass measurements

These numbers come from the DeepInfer second pass described in the [reference review](deepinfer-review.md#second-pass-algorithm-level-patterns).
They were taken on a Linux x86-64 container, not the Windows workstation used above, so compare
rows within this section only. Before is revision `265444c` with the UI redesign applied. After
is the current tree. Each operation was run once as warmup, then five times; the tables show
medians. The large workload is the same one used above.

### Service

| Large workload | Before | After |
| --- | ---: | ---: |
| Full index rebuild, new `Vault` (parse, validate, collect, write) | 3,495 ms | 239 ms |
| Full index rebuild, warm snapshot | 3,495 ms | 79 ms |
| Assemble viewer day | 107 ms | 35 ms |
| Collect index evidence | 69 ms | 18 ms |
| Events overlapping a 15-minute window | — | 0.17 ms |
| Serialized `/day` payload, first request after a change | — | 191 ms |
| Serialized `/day` payload, unchanged day | — | 0.63 ms |
| Warm retrieval | 0.72 ms | 0.59 ms |

On the original revision, the rebuild had no warm state: every call parsed and validated again.
Retrieval was left as it was; sub-millisecond differences are noise. Assembling the day now
round-trips through the cached payload so callers never share objects with the cache. The benchmark ran with
`--compare`, so day, evidence, build and retrieval output hashes matched the before run. Raw
samples and source hashes are in [before](../eval/results/core-snapshot-before.json) and
[after](../eval/results/core-snapshot-after.json).

The same workloads went through FastAPI's `TestClient`; each figure is the median of five requests
on the large day:

| Endpoint | Before | After |
| --- | ---: | ---: |
| `GET /day/{date}` | 549 ms | 2.7 ms |
| `GET /day/{date}/events`, 30-minute window | 55 ms | 3.7 ms |
| `POST /ask` | 4.0 ms | 4.0 ms |

About 2.5 ms of each "after" figure is the test client's own overhead.

### Viewer playback

Four seconds of 1× playback on the sample day, 1600×1000 at device scale factor 1. Headless
Chromium used SwiftShader, its software renderer. Three runs; the figures are medians:

| Measurement | Before | After |
| --- | ---: | ---: |
| Main-thread script time | 3,782 ms | 171 ms |
| Main-thread task time | 3,824 ms | 234 ms |
| Tasks longer than 50 ms | 9 | 0 |
| Shader programs linked during 3 s of playback | 8 | 0 |
| Buffer allocations (`bufferData`) during 3 s of playback | 120 | 0 |
| WebGL draw calls per rendered frame | 13.6 | 10.1 |

The draw-call row counts every pass. It includes the shadow map, about two draws, which
refreshes as the sun moves. three.js counts only the main pass, and that count fell from 12–13 to
8, which is the budget shown in Settings › Performance. The software renderer caps the frame rate
at about 6 fps whichever version runs. The main thread was saturated before and is now 94% idle,
so the gain shows up as headroom rather than frames in this setup. Real GPUs still need their own
traces.

Collect the same playback sample with the dev server running:

```powershell
node scripts/profile-viewer.mjs current playback
```

The viewer e2e suite asserts zero shader links and buffer allocations during playback. It also
checks the 8-call budget.

## Storage and caching pass

Storage per day at spec capture rates, the cache design and the measurements for appends,
conditional day loads, path simplification, OCR runs and compaction are in
[storage and caching](storage.md). Two benchmark outputs change on purpose in that pass:
- the viewer day, because paths are simplified to 10 cm and reduced to the viewer's fields;
- the index, because a block repeated on consecutive screenshots is one unit.

Every other output hash matches. Use [core-storage.json](../eval/results/core-storage.json) as the
`--compare` baseline from here on.

## Queued index burst

The scheduled follow-up measured repeated same-day index submissions. Previously, every request
created a background task that repeated the full rebuild behind the vault's single-writer lock.
`IndexJobs` now shares active requests for the same source version, synchronizes admission/status
changes, caps distinct active jobs at eight and retains at most 100 records. Source changes get
their own job; completed or failed work never suppresses a later explicit rebuild.

Same Windows workstation, Python 3.11.9 and SQLite 3.45.1. The workload is 50 visits, 5,000
events, 10,000 fixes and 1,000 screenshots. Each sample first submits 20 requests to the API
handler, then runs their background tasks concurrently. App construction and a warm index build
are excluded. One warmup burst precedes five measured bursts; the table reports medians.

| Queued burst of 20 requests | Before | After |
| --- | ---: | ---: |
| Rebuilds executed | 20 | 1 |
| Distinct returned job ids | 20 | 1 |
| Submission + background completion | 994.15 ms | 58.91 ms |

Every returned job reaches `complete`. All stored unit rows have the same SHA-256 fingerprint
before and after, and FTS external-content integrity checks pass. Raw samples, source hashes and
runtime metadata are in [before](../eval/results/jobs-before.json) and
[after](../eval/results/jobs-after.json). This is a queued-burst measurement through direct handler
calls, not HTTP/auth latency, individual click latency, browser frame rate or DeepInfer throughput.
Requests arriving after a job completes intentionally start another build. Jobs read current
files when they run; they do not retain a frozen snapshot or survive process restart.

```powershell
npm run bench:jobs -- --output test-results/jobs-current.json --compare eval/results/jobs-before.json
uv run pytest services/ingest/tests/test_jobs.py services/ingest/tests/test_job_api.py
```

Deterministic tests cover 16 competing submitters, exactly one scheduling owner, concurrent
capacity limits, non-blocking status/admission during a build, source-version changes, failure
recovery, detached result ownership and history eviction when keys are reused. HTTP tests verify
duplicate ids, source changes and `429`/`Retry-After` admission behavior. The full validation run
passed 107 Python tests, four contract tests, two API browser tests, eight viewer browser tests,
Ruff and the production build. Durable queueing, cancellation and continuous-capture
backpressure remain separate work.


# Viewer rendering performance

The viewer uses demand-driven rendering. A paused diorama should finish camera easing and stop
drawing. OrbitControls invalidates frames while orbiting/zooming; playback requests animation
frames while it runs. The scene and timeline pause while a modal is open or the page is hidden.
The 2D fallback redraws for data/time/camera changes and resize, rather than running an idle loop.

The default render resolution is capped at one device pixel per CSS pixel. Directional shadows
use a cached 512px map refreshed for scene, light/time, cutaway and floor changes. The separate
contact-shadow pass was removed. Large backdrop blurs were replaced with opaque panels; tilt-shift
and mouse parallax remain opt-in. Hover motion values avoid React updates on every pointer move.
The orthographic nameplate uses fixed pixel sizing rather than a distance factor that magnified
its DOM surface hundreds of times. The FPS readout no longer clamps low measurements to 30.

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
playback renders, modal background rendering stops, the nameplate remains normal sized, and API
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

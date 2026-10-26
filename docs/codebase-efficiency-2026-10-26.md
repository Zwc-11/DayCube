# Codebase efficiency pass

Completed 2026-10-26, continuing the 2026-10-25 measurements. Starting source:
`188765c`. Earlier reports retain their original dates, source hashes and workload definitions.
Integration includes the subsequent Spark/Three.js and camera-loop changes through `6ecac86`;
those upstream changes are preserved, rather than attributed to this pass.

## Reference and coverage

Reference: [DeepInfer at 5537415](https://github.com/deepseek-ai/DeepInfer/tree/553741557a57df44a46fb3ab72b6aafeaf945337).
The recursive inventory has 398 entries. Read these current implementations:

- `csrc/utils/object_pool.hpp`: explicit release and reuse.
- `deepinfer/worker/cuda_graph_runner.py`: preparation separated from replay, shared backing buffers,
  bounded batch sizes and cleanup.
- `deepinfer/model_executor/layers/mtp_info_pool.py`: capacity and reserved slots are explicit.
- `deepinfer/worker/decode_perf_cache.py`: complete cache keys, validation and atomic publication.
- `examples/sweep/run_sweep.sh`: named sweeps, retained provenance and failed-run rejection.
- `csrc/engine/scheduler.hpp`: sampled construction, capacity reservation and release paths.

This is a reference-pattern review, not a line-by-line DeepInfer audit or a reproduction of its
accelerator results. DAYCUBE's browser/storage workloads do not establish throughput parity with it.

The refreshed [ledger](review-ledger.json) inventories every tracked file, including the new phone,
speech, graph and job modules. It distinguishes syntax/structure checks, hash-matching manual reviews,
stale reviews and unreviewed implementations. Binary fixtures, assets, generated contracts and
lockfiles have separate treatment. Unrelated untracked capture-benchmark drafts are preserved and
excluded. An inventory is not a complete semantic audit of every algorithm or dependency.
The final inventory covers 472 files: 200 retain hash-matching, scoped manual notes; 63 have
older notes requiring refresh. There are 118 source/test files without a current manual review.
Those are explicit unfinished semantic coverage, even where the structural checks and suites pass.

| Area | Review and action | Limit |
| --- | --- | --- |
| Viewer, graph and shared UI | Lazy scene entry, typed layout/adjacency buffers, demand drawing and resource scopes; reuse label ordering/candidates and hover masks | Real-GPU and hours-long session profiling remains open |
| API, storage, search and notes | Existing snapshot/transaction/cache boundaries retained; stream capture logs and cache their text projection | History discovery and directory freshness scans still grow with the vault |
| Capture, gaze and Opera | Existing schema boundaries, batching, latest-frame processing and queue caps preserved; scripted regressions included | No live camera, calibration, recording or Opera session was changed |
| Phone, media and splats | Preserve native subprocess stages, units, source recordings and explicit failures; separate scheduling tests from optional mesh reconstruction | Real reconstruction and sustained GPU utilization are unmeasured here |
| Jobs and speech | Index per-input history; examine leases, batching and model lifetime boundaries; add model-free speech tests to the common gate | CPU model process copies, GPU residency and actual recognition performance need dedicated measurements |
| Schemas, tooling and fixtures | Canonical contract generation and transport checks retained; inventory understands binary fixtures and can exclude unrelated drafts | Dependency internals and generated output algorithms are not independently audited |

## Capture projection

Profiles showed JSON decoding of repeated geometry snapshots dominating capture indexing. Consumers
need the document's latest URL/nonempty title and each block's latest text, not retained geometry.
The reader now streams plain JSONL records and discards each geometry record after projecting it.
A vault-owned segmented LRU retains only those projections, with a 32 MiB accounted-memory budget.

Keys cover the logical write version and the physical file's identity, size and modification time.
Appending, replacing, removing, compacting and recreating a source invalidate its entry. A source
that changes while being projected is not admitted under its earlier key. Returned maps are copies;
callers cannot modify cached maps. Same-size in-process writes use the vault's write counter.

Midnight inheritance is preserved: a position-only snapshot today can inherit yesterday's text and
title. Compacted frames keep the existing complete-frame integrity check; truncated compressed data
still fails explicitly. Plain live logs stream, while compressed frames are decoded before iteration.

Named fixture: 120 documents, 32 blocks per document, 4,000 previous-day snapshots and 400 current-day
snapshots; 11,745,348 bytes initially, all synthetic. Seven timed repetitions after one warmup on the
Windows Ryzen 7 9800X3D workstation, Python 3.11.9, warm filesystem. Hashing is outside timing.

| Operation | Before median | After median |
| --- | ---: | ---: |
| Cold capture parsing/evidence | 127.10 ms | 60.63 ms |
| Unchanged capture parsing/evidence | 166.47 ms | 0.68 ms |
| Search after a position-only append | 180.04 ms | 19.91 ms |

Cold traced peak fell from 68.70 MB to 5.00 MB. The warm fixture retains 4,090,028 accounted cache
bytes. This is not process RSS or a universal memory ceiling: an oversized working projection can
exceed the retained-cache budget and is not cached. External same-size edits that deliberately
restore every file stamp are outside the stamp contract; atomic replacement or vault writes remain
the supported way to publish changes.

Evidence IDs, timestamps/offsets, confidence, fidelity and all other semantic fields match. Search
fingerprints omit only SQLite's regenerated `unit_id`; full capture structures are compared too.
No HTTP or embedding model is in this fixture. Appending still reparses the changed file; this pass
does not introduce a mutable incremental-log state machine.

Reports: [before](../eval/results/capture-projection-before-2026-10-25.json),
[after](../eval/results/capture-projection-after-2026-10-25.json).

## Graph redraws

The graph already batches edges/nodes and stops at rest. Redraws still created label objects and
sorted them again. `GraphLabels` prepares degree/tie order when the visible model changes and owns
four fixed candidate buckets plus exact double-precision alpha storage. Frames refill those buffers.
The hovered neighbourhood mask changes with the model/hovered node, rather than each fade frame.
Other draw paths, fonts, colors, label clipping, overlap rules, camera/layout and interactions remain
the same. The candidate storage is linear in visible node count; it is released with that view.

Named fixture: actual GraphCanvas with 6,000 synthetic notes, deterministic saved positions,
1600×1000 viewport, device scale 1, 61 redraw samples after five warmups. Headless Chromium
156.0.8078.4 used ANGLE/SwiftShader; Node 24.11.1. The development server is isolated from the API.

| Operation | Before median | After median | Before sampled allocation | After sampled allocation |
| --- | ---: | ---: | ---: | ---: |
| Selection changes | 0.70 ms | 0.70 ms | 12.21 MB | 12.80 MB |
| Broad search highlighting | 2.00 ms | 1.70 ms | 35.66 MB | 16.98 MB |

Broad-search sampled p95 changed from 2.5 to 2.3 ms. Exact canvas pixel hashes match in both states;
idle draws remain zero. Selection timing did not improve, and its statistical allocation sample was
slightly larger. The useful result is reduced broad-search allocation, not a general frame-rate claim.
Samples cover the draw callback; waiting, React work and GPU latency are separate. Heap sampling
includes collected objects and the rest of the page. Path2D and collision-grid allocations remain.

Reports: [before](../eval/results/graph-draw-before-2026-10-25.json),
[after](../eval/results/graph-draw-after-2026-10-25.json).

After integrating `6ecac86` and its locked dependencies, a separate
[integration run](../eval/results/graph-draw-integrated-2026-10-26.json) retained both pixel hashes
and zero idle draws. Selection measured 0.70 ms median/1.10 ms p95; broad search measured
1.80 ms median/2.40 ms p95 with 17.03 MB sampled allocation. This is another isolated run,
not a replacement for the original paired reports. All capture/job after-report source hashes
and this integration report's source hashes match the final working sources.

## Job history

Per-moment status reads filtered by stage and serialized inputs while the old index began with
status. SQLite scanned the job table. An additive `(stage, inputs, id DESC)` index now serves the
lookup in its requested order. Existing databases acquire it on open, without replacing rows or
changing leases, fairness, retries, output/error states or stage execution.

| Synthetic completed jobs | Before median | After median |
| --- | ---: | ---: |
| 1,000 | 0.0526 ms | 0.0102 ms |
| 50,000 | 4.6053 ms | 0.0254 ms |

Eleven repetitions after one warmup, one warm connection. Result hashes match. The query plan changes
from `SCAN jobs` to `SEARCH jobs USING INDEX jobs_input`. At 50,000 rows, database size increases
from 13,352,960 to 17,162,240 bytes. Writes/index creation are outside timing; this is a deliberate
storage/write-cost tradeoff and does not measure faster model execution.

Reports: [before](../eval/results/job-lookup-before-2026-10-25.json),
[after](../eval/results/job-lookup-after-2026-10-25.json).

## Validation and remaining work

The broad small/large core benchmark preserves every operation's output hash and complete stored
index/FTS integrity against source archived from the actual starting revision `188765c`:
[baseline](../eval/results/core-codebase-before-2026-10-25.json),
[current](../eval/results/core-codebase-after-2026-10-25.json). The October 24 report no longer matches
intervening product changes, so it was not rewritten or used to dismiss drift. No broad core speedup
is claimed from those short samples.

Focused tests cover midnight text/title inheritance, malformed/torn records, append/rewrite/remove,
compressed-file integrity, ownership, cache budgets, existing job-index migration and deterministic
label ordering over 200 changing frames. Core speech tests use stand-ins, with model canaries gated;
the speaker-clustering test needs scikit-learn in the separate speech environment. The mesh scheduling
test simulates both unavailable and available tools; real geometry tests still require Open3D.

Final local validation on the integrated branch:

- `npm run check`: Ruff, contract generation, TypeScript and production build pass;
  338 core tests pass (7 optional-tool/platform skips), 140 gaze tests, 4 contract tests,
  9 transport tests, 10 extension tests, 2 label-buffer tests and 8 model-free speech tests pass.
  Core speech skips 3 model canaries and optional speaker clustering; its separate environment
  also passes the clustering case (9 pass, 3 canary skips).
- All 35 viewer, 2 API and 5 extension reporter browser tests pass with synthetic fixtures.
- Receiver wheel/source packaging and `python -m capture --help` pass. The receiver's 18 Python
  modules are included, with no runtime data. Live receiver/extension acceptance is not run;
  it would require the user's recording session and port 7700.
- FFmpeg/ffprobe 9.0.2 are available on the test processes' PATH. No global PATH change is needed.
  Vite reports large Three.js/Spark chunks (964 kB/2.49 MB uncompressed). They remain lazy scene
  dependencies; this pass does not claim faster 3D startup or hardware-GPU performance.

Repeat separately from correctness gates:

```powershell
uv run python eval/benchmark_capture_projection.py --output test-results/capture-current.json --compare eval/results/capture-projection-before-2026-10-25.json
node eval/benchmark_graph_draw.mjs --output test-results/graph-current.json --compare eval/results/graph-draw-before-2026-10-25.json
uv run python eval/benchmark_job_lookup.py --output test-results/jobs-current.json --compare eval/results/job-lookup-before-2026-10-25.json
uv run python scripts/review-inventory.py --tracked-only
npm run check
npm run test:e2e:viewer
```

Current limits remain explicit: whole-history freshness scans, real-GPU startup/rendering and long
sessions, aggregate memory under concurrent clients, optional model CPU/GPU/IPC costs, live capture
acceptance and browser/receiver byte budgets need further representative traces. This pass does not
replace unreviewed ledger entries with a blanket claim of optimal algorithms or DeepInfer parity.

# DeepInfer reference review

Reference: [deepseek-ai/DeepInfer](https://github.com/deepseek-ai/DeepInfer), revision
[`774ce244b87cc0eb8d4b94ab9f5de2ab22049f01`](https://github.com/deepseek-ai/DeepInfer/tree/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01).

## Scope and conclusion

Inspected the complete recursive repository inventory (342 files plus six submodule pointers),
the README, and representative code across configuration, memory management, graph execution,
kernel interfaces, tests, caching and benchmark tooling. Ten files were read in full; the engine,
scheduler, public LLM wrapper, statistics logger and device abstraction were sampled at their
entry sections. This is an architectural and implementation review, not an audit of every line,
vendored dependency or hardware benchmark.

The code shows serious attention to inference efficiency: preallocated buffers, explicit object
lifetimes, batch-oriented interfaces, native scheduling and specialized kernels. Its published
throughput remains an upstream claim; it was not reproduced on this Windows workstation.
DAYCUBE can adopt the engineering discipline and verify its own performance independently.

## Patterns worth adopting

| Reference evidence | What it does | Application to DAYCUBE |
| --- | --- | --- |
| [Object pool](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/csrc/utils/object_pool.hpp) | Explicit allocation/release and reusable backing storage | Reuse render resources; define ownership and cleanup for connections, listeners and timers |
| [CUDA graph runner](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/deepinfer/worker/cuda_graph_runner.py) | Separates preparation, warmup, capture and replay; preallocates buffers | Prepare scene data outside animation frames; invalidate cached rendering when inputs change |
| [MTP pool](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/deepinfer/model_executor/layers/mtp_info_pool.py) | Documents buffer capacity and reserved padding slots | Make capacity, memory cost and overflow behavior explicit before continuous capture |
| [RMSNorm interface](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/deepinfer/model_executor/layers/layernorm.py) | Small typed wrapper over optimized operations; optional output reuse | Keep typed public boundaries; optimize internal implementation without changing contracts |
| [Decode performance cache](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/deepinfer/worker/decode_perf_cache.py) | Keys include configuration/units; validates cached contents and uses atomic replacement | Treat cache keys/invalidation as correctness; preserve atomic vault/index writes |
| [Synchronization guard tests](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/tests/test_cuda_sync_guards.py) | Checks nesting, restoration and thread interleaving; includes lightweight fake-device tests | Test failure recovery, deterministic lookup ties and rebuild correctness without cloud dependencies |
| [Benchmark sweep](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/examples/sweep/run_sweep.sh) | Sweeps workload parameters, records configuration/revision, disables profiling for timing, rejects failed sweeps | Benchmark multiple day sizes, retain raw samples and result fingerprints, fail on output drift |
| [Contributing guide](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/CONTRIBUTING.md), [tool configuration](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/pyproject.toml), [Makefile](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/Makefile) | Shared formatting/lint rules; separates core tests from expensive validation | Maintain reproducible commands and separate browser/core checks from optional AI/GPU pipelines |

The sampled [engine](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/deepinfer/engine/llm_engine.py)
coordinates Python with native scheduling, worker execution and explicit deferred resource release.
The sampled [scheduler](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/csrc/engine/scheduler.hpp)
validates incompatible configurations and reserves vector capacity up front. The
[public wrapper](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/deepinfer/entrypoints/llm.py)
accepts batched input; the
[statistics logger](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/deepinfer/engine/stats_logger.py)
defines per-step measurements and ownership; the
[device module](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/deep_cluster/hardware/device.py)
isolates hardware discovery behind typed functions.

## What should not be copied indiscriminately

DeepInfer targets distributed accelerator inference; DAYCUBE is a browser application with a local
Python/SQLite service. C++/CUDA, distributed collectives and inference-specific memory pools do
not make ordinary UI or vault operations faster automatically. Some reference files are large
(the scheduler is about 66 KB and the engine 45 KB), and some boundary handlers catch broad
exceptions. File size, syntax density and the absence of all allocations are not quality scores.
Adopt measured tradeoffs and explicit invariants; retain language-appropriate conventions.

## First implementation pass

The backend changes motivated by this review target repeated scans and row-at-a-time indexing:

- Group events and path fixes once when assembling viewer layers, preserving their ordering.
- Parse event times once into a sorted nearest-event lookup instead of parsing/scanning every
  event for every screenshot. Keep the ten-minute cutoff, earlier-time tie rule and first record
  at duplicate timestamps.
- Batch unit inserts and rebuild the FTS index once within the existing transaction. Close the
  temporary database before atomic replacement, including on failure.
- Add correctness tests and an isolated scale benchmark with source hashes and output fingerprints.

## Second pass: algorithm-level patterns

A closer read of the scheduler, input preparation and tests turned up six concrete techniques.
Each one is applied below where a profile showed the matching cost in DAYCUBE.

| Reference evidence | Technique | DAYCUBE change |
| --- | --- | --- |
| [`zos.cpp` sizes `total_q`](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/csrc/engine/zos.cpp#L213-L233), [allocates once](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/csrc/engine/zos.cpp#L51) and [checks the fill](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/csrc/engine/zos.cpp#L80) | Count, allocate exactly, fill, assert the count | Pin stems, transit arcs and venue edges are built into exactly sized `Float32Array`s, one `lineSegments` each. Discs and venue nodes are one `InstancedMesh` each |
| [Scheduler constructor](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/csrc/engine/scheduler.hpp#L121-L131), [object pool](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/csrc/utils/object_pool.hpp) | Reserve capacity up front and reuse storage in place | The 3D frame loop allocates nothing: `samplePath` writes into a caller-owned sample, as RMSNorm accepts `out=`. Geometry is rebuilt only when its inputs change, and disposed by the scope that built it |
| [Checkpoint queue](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/csrc/engine/scheduler.hpp#L478-L481) | Use the ordering invariant and look only at the head | `EventSeries` keeps events sorted by start time and records the longest span. A window query bisects to `t0 - max_span`, so it never scans the whole day. The viewer looks up layers, path samples and breadcrumbs by binary search |
| [Decode perf cache key](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/deepinfer/worker/decode_perf_cache.py#L40-L52) and [atomic write](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/deepinfer/worker/decode_perf_cache.py#L146-L161) | A cache key built from every input; replacement is atomic | `DaySnapshot` keys on `(mtime_ns, size, inode)` for every file it reads, plus a per-path write counter. The counter stops a same-size rewrite within one coarse clock tick from serving stale data. `/day` bytes, validation results and index freshness are all memoized on that key |
| [`TORCH_CHECK` at construction](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/csrc/engine/scheduler.hpp#L115-L120) | Fail at the boundary with the exact cause | Each snapshot section is schema-checked when it loads. `ContractError` carries the contract name and JSON path, and the API returns it as a 422 |
| [Sync-guard tests](https://github.com/deepseek-ai/DeepInfer/blob/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01/tests/test_cuda_sync_guards.py) | Fakes and adversarial interleavings instead of the real device | The `test_snapshot.py` tests cover several cases. A property test checks the window query against a full scan, and another rewrites files behind the cache to simulate an outside editor. Others cover a same-size rewrite and carrying validation through an append |

Two more changes came out of profiling rather than from the reference:

- **Native validation on the hot path.** On a 5,000-event day, pure-Python `jsonschema` took
  96% of an index rebuild. `jsonschema-rs` compiles the same Draft 2020-12 schemas once per
  process, with `format` checking on. The schemas stay canonical.
- **Viewer playback.** drei `<Line>` disposes its material when the identity of `points` changes.
  Recomputing points on each playhead tick therefore relinked shader programs mid-playback. A
  memoized geometry per batch, plus stable callbacks across `React.memo` boundaries, removes both
  the relinks and the re-renders.

The lint configuration now matches the reference rule families: `E`, `F`, `UP`, `B`, `SIM`, `I` and
`ANN`, without its CUDA-specific ignores. Measurements are in [performance notes](performance.md#second-pass-measurements).

## Gaze processing rate

The Windows gaze pass revisited the pinned CUDA graph runner's separation of owned buffers from
per-step work. DAYCUBE keeps EyeTrax's native landmark extraction and calibrated model unchanged;
the tracker reuses one prediction input matrix, releasing it with the camera. Pacing and FPS
telemetry use a monotonic timer, while evidence timestamps keep their wall-clock timebase.

Profiling at 640×480 found about 4.7–4.9 ms/frame in native landmarks and 0.25–0.30 ms/frame
in prediction. The existing synchronous path processed about 30 frames/s with either Windows
backend, so adding capture threads or a GPU inference dependency was not justified. The live
tracker's 14.8 frames/s reflected its configured 15 frames/s cap. Its default target is now 30,
with a separate measured rate, invalid-rate checks and tests for unchanged synthetic positions.
The gain from raising the cap must not be attributed to the small input-buffer allocation saving.

This pass also fixes stale "tracking" status during pause and adds measured/target FPS and face
percentage to the extension options. See [capture rate checks](capture.md#processing-rate-and-live-checks).
These are camera/CPU workload improvements, not evidence of accelerator throughput parity.

The [engineering standard](engineering.md) defines the ongoing expectations and remaining gaps.
The earlier viewer fixes and idle measurements are documented separately in
[performance notes](performance.md). Neither pass establishes general performance parity
with DeepInfer or finishes the remaining live-mode and capture work.

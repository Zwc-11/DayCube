# Codebase efficiency review

The [2026-10-24 follow-up](review-2026-10-24.md) adds a hash-bound file ledger, notes/graph/cache
measurements, concurrent-job and retrieval fixes, extension delivery checks and explicit live-write
failures. The review below is historical; its test counts and open issues describe that earlier pass.

Date: 2026-10-23. Baseline: `877099f`, with newer capture work preserved. Reference:
[DeepInfer at the pinned revision](https://github.com/deepseek-ai/DeepInfer/tree/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01).

The review covers module boundaries and frequently executed paths across the repository.
It does not cover every source line, dependency or optional GPU kernel. DeepInfer supplied
design patterns; its throughput has not been compared with DAYCUBE. See the earlier
[reference review](deepinfer-review.md) for the upstream files examined.

## Coverage and ownership

| Area | Reviewed behavior | Evidence or remaining limit |
| --- | --- | --- |
| Viewer and route sampling | Demand rendering, route buffers, geometry disposal, callbacks and paused/hidden views | Browser tests cover rendering, buffer reuse, walking and sync. Large-day traces and 2D allocation profiles are still needed. Visual design is unchanged. |
| API client and frontend bridge | Response validation, conditional day loads, incremental SSE and reader cleanup | Events arrive before EOF; the final answer is validated. Event buffering is bounded. Invalid responses do not trigger a duplicate request. |
| Ingest API and storage | Atomic writes, append-only logs, snapshot invalidation, SQLite transactions and bounded job history | Core and browser checks pass. Concurrent clients and repeated indexing of the same day need a load test. |
| Retrieval and vector index | Per-day enrichment, scoped loads, cache invalidation and memory budget | Correctness tests and search measurements are recorded below. |
| Windows capture and gaze | Environment isolation, queue limits, camera release, fixation buffers and inference | Existing capture changes are preserved. Scripted checks pass; manual Windows acceptance is incomplete. |
| Opera extension | Worker delivery, capped queues, text epochs and focus changes | Library and reporter tests pass and are included in CI. Full extension/receiver tests were skipped because the live receiver occupies port 7700. |
| Shared schemas | JSON Schema and generated Python/TypeScript bindings | Schema consistency, contract tests and TypeScript checks are in `npm run check`. |
| Media, GPX, venue and floor helpers | Subprocess timeouts, streamed XML, atomic caches, confidence and fidelity | Existing correctness tests and Ruff pass. Adapters were not run against personal media or live location. |
| Optional cloud/model jobs | Lazy SDK imports, input limits, temporary files and depth units | Source review only. GPU batching, model residency, allocator usage and cloud latency are unmeasured. |
| Evaluation, scripts and CI | Reproducible fixtures, output hashes, isolated environments and test artifacts | Benchmarks run separately from correctness checks. Browser suites have separate output directories to preserve each other's results. |

## Search measurements

Returning ten records from one day loaded the snapshot ten times and checked names ten times.
Retrieval now prepares each day once and reads names once per query. On this workload,
`snapshot` calls fell from 10 to 1, `snapshot_key` from 11 to 2 (including the freshness check),
and `place_names` from 10 to 1.

Vector search loaded every indexed day even for a one-day query. It now loads the requested
days into a segmented LRU, keyed by the database's non-reused generation. The 64 MiB cache
budget accounts for array bytes and a fixed allowance per entry. A larger day can exceed that
budget while being searched, but is not cached. Candidate arrays do not retain evicted blocks.
Sign-bit packing also avoids a NumPy conversion for each database row.

On this Windows PC (Ryzen 7 9800X3D), five repetitions after one warmup:

| Synthetic workload | Before median | After median |
| --- | ---: | ---: |
| Keyword query returning ten evidence records from the large fixture | 11.32 ms | 7.75 ms |
| Cold vector index, one day scoped from 100 days | 102.33 ms | 1.25 ms |
| Cold vector index, all 100 days | 104.11 ms | 82.97 ms |

The vector fixture uses 100,000 seeded random 384-dimensional int8 vectors, `k=10`, seed
20261023 and warm in-memory SQLite. Each sample starts with a cold index. Fixture creation
and output hashing are outside the timings. Ranked-output hashes match for all three workloads.
The one-day search previously retained 6,400,000 array bytes; the new cache charges 64,512 bytes,
including its entry allowance. This measures cache retention, not process peak memory.
Real-embedding recall and end-to-end HTTP latency were not measured.

Reports: [before](../eval/results/search-before.json), [after](../eval/results/search-after.json).
Repeat with:

```powershell
uv run python eval/benchmark_search.py --output test-results/search-current.json --compare eval/results/search-before.json
```

The existing small/large core benchmarks also preserved outputs for day assembly, indexing,
retrieval, event windows and serialized payloads. Their timings varied, with no clear speedup.

## Streaming

The frontend bridge awaited `response.text()`, so it could not use an early location event
until the whole response ended. The API client now parses events as they arrive and releases
the reader on completion or cancellation. The regression test holds the stream open after
the location event and checks that the location is delivered while the answer is pending.
This tests event ordering. Server retrieval still finishes before streaming begins.

## Checks and remaining work

Run `npm run check` before merging. It runs Ruff, schema consistency, contract and transport
tests, core tests, gaze tests in their own environment, extension library tests, TypeScript
and the viewer build. CI is configured to run this command on Windows and Linux, with
API/viewer/reporter browser tests on Linux.

Further profiling is needed for long viewer sessions, GPU use, startup cost of the 833 kB
Three.js chunk, 2D redraw allocations, freshness scans over years of days and secondary metadata
caches. Concurrent indexing, failed live mutations and optional model jobs also need testing.

Local validation: 148 core tests, 33 gaze tests, 4 contract tests, 9 transport tests,
6 extension library tests, 11 viewer browser tests, 2 API browser tests and 2 reporter browser
tests passed (215 total). Ruff, TypeScript, schema generation consistency, receiver package
archive checks and the production build passed. These are local Windows results; remote CI
results have not been verified.

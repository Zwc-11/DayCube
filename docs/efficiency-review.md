# Codebase efficiency review

Date: 2026-10-23. Baseline: `877099f`, with newer capture work preserved. Reference:
[DeepInfer at the pinned revision](https://github.com/deepseek-ai/DeepInfer/tree/774ce244b87cc0eb8d4b94ab9f5de2ab22049f01).

The engineering standard applies to every DAYCUBE module. This pass inventories all source
areas and reviews their architecture and frequently executed paths. It is not a line-by-line
audit of every source file, dependency or optional GPU kernel, and does not establish throughput
parity with DeepInfer. The earlier [reference review](deepinfer-review.md) records its own scope.

## Coverage and ownership

| Area | Reviewed behavior | Evidence or remaining limit |
| --- | --- | --- |
| Viewer, 3D/2D and route sampling | Demand rendering, caller-owned route samples, geometry disposal, stable callbacks, modal/hidden stopping | Viewer tests cover idle stopping, redraws, shader/buffer reuse, draw calls, walking and sync. Large-day interactive traces and allocation profiling of the 2D fallback remain open. No visual design changes. |
| API client and frontend bridge | Validation at the transport boundary, conditional day loads, incremental SSE, reader lifetime | Streaming now consumes events on receipt, validates the final answer, bounds event buffering and releases the reader. Invalid responses fail without a duplicate JSON request. |
| Ingest API and storage | Atomic writes, append-only logs, snapshot invalidation, SQLite transactions and connections, finite job history | Existing core and browser checks pass. Same-day background-job coalescing and admission policy under concurrent clients still need a load test. |
| Retrieval and vector index | Group per-day enrichment once, scoped vector loads, generation invalidation, retained cache budget | New deterministic checks and a repeatable synthetic benchmark. Detailed results below. |
| Windows capture and gaze | Separate environments, queue/batch limits, camera release, fixation buffers and native inference | Existing newer capture improvements retained. Scripted correctness checks pass; Windows acceptance remains separate and is not replaced by simulations. |
| Opera extension | Worker delivery ownership, capped queues, text epochs, focus invalidation and guards | Library and focused reporter tests pass and run in CI. Full extension/receiver e2e needs port 7700 free; the live receiver was not stopped for it. |
| Shared schemas and generated bindings | JSON Schema remains canonical; matching Python/TypeScript generation | Schema consistency, contract tests and TypeScript checks run in the common gate. |
| Media, GPX, venue and floor helpers | Explicit subprocess timeouts, streamed XML parsing, atomic adapter cache writes, confidence/fidelity boundaries | Existing helper correctness tests and Ruff apply. No personal media or live location was sent to adapters during this review. |
| Optional cloud/model jobs | Lazy SDK boundary, finite input limits, temporary-file lifetime, explicit relative-depth units | Inspected definitions only. Model residency, GPU batching, allocator usage and cloud latency remain unmeasured; nothing was deployed or invoked. |
| Evaluation, scripts and CI | Synthetic fixtures, output fingerprints, isolated environments and artifact ownership | `npm run check` covers all deterministic local layers. Benchmarks stay separate. Browser suites use distinct output directories so one suite does not erase another suite's measurements. |

## Changes measured in this pass

The search profile found ten snapshot loads and ten name-cache checks for ten ranked records
from the same day. Enrichment now prepares each distinct day once and reads names once. On
the measured query this reduces `snapshot` calls from 10 to 1, `snapshot_key` calls from 11
to 2 (including freshness), and `place_names` calls from 10 to 1.

The vector index previously loaded every indexed day before applying a one-day scope. It now
loads only requested days, keyed by the database's non-reused generation, into the existing
segmented LRU with a 64 MiB budget. Native array bytes plus a fixed per-entry allowance are
charged to that budget; this is not an RSS cap. A single oversized day's transient working
block can exceed it but is not retained. Candidate arrays do not keep evicted blocks alive.
Packing sign-bit rows also avoids a NumPy conversion for every database row.

On this Windows PC (Ryzen 7 9800X3D), five repetitions after one warmup:

| Synthetic workload | Before median | After median |
| --- | ---: | ---: |
| Keyword query returning ten evidence records from the large fixture | 11.32 ms | 7.75 ms |
| Cold vector index, one day scoped from 100 days | 102.33 ms | 1.25 ms |
| Cold vector index, all 100 days | 104.11 ms | 82.97 ms |

The vector fixture contains 100,000 deterministic random 384-dimensional int8 vectors;
`k=10`, seed 20261023, warm in-memory SQLite. Fixture creation and output hashing are outside
the timings. A cold index is recreated for each sample. All three ranked-output fingerprints
match before and after. The one-day search retained 6,400,000 array bytes before; after it
charges 64,512 bytes including the fixed entry allowance. These figures describe index retention,
not whole-process peak memory. This synthetic vector fixture measures plumbing, not recall on
real embeddings or end-to-end HTTP latency.

Reports: [before](../eval/results/search-before.json), [after](../eval/results/search-after.json).
Repeat with:

```powershell
uv run python eval/benchmark_search.py --output test-results/search-current.json --compare eval/results/search-before.json
```

The established small/large core benchmark also passed unchanged-output checks for day assembly,
indexing, retrieval, event windows and serialized payloads. Its unrelated timings varied; this
pass makes no speed claim for them.

The streaming regression deliberately withholds EOF after the location event. The original
bridge failed because it awaited `response.text()`; the new API-client stream delivers the
location while the answer is still pending. This is a deterministic ordering check, not a
network latency benchmark. The server still computes its extractive result before streaming
it; this client change does not speed up server-side retrieval by itself.

## Continuing requirements

Run `npm run check` before merging: Ruff across Python source/scripts/jobs/evaluation, shared
schema consistency, contract and transport tests, API/core tests, the gaze environment's tests,
extension library tests, TypeScript and the production viewer build. CI runs this gate on
Windows and Linux and adds API/viewer/reporter browser checks on Linux.

Use workload measurements to choose further changes. Remaining targets include representative
GPU traces and long sessions, the 833 kB Three.js production chunk's startup cost, 2D redraw
allocations, all-vault freshness scans on years of days,
budgets for secondary metadata caches, concurrent indexing admission/coalescing, live mutation
failure handling in the viewer, and measured model residency/batching in optional cloud jobs.
The vector retained-cache budget does not resolve these separate limits. A lint/build pass or
one successful benchmark does not mean every module has reached its possible maximum efficiency.

Validation for this pass: 148 core tests, 33 gaze tests, 4 contract tests, 9 transport tests,
6 extension library tests, 11 viewer browser tests, 2 API browser tests and 2 reporter browser
tests passed (215 total). Ruff, TypeScript, schema generation consistency, receiver package
archive checks and the production build passed. The local Windows results do not assert that
the newly expanded remote Windows/Linux CI matrix has already completed.

# DAYCUBE engineering standard

Use DeepInfer as an engineering reference: explicit ownership, batched execution, clear execution
boundaries and repeatable performance evidence. The [reference review](deepinfer-review.md) records
the revision and files inspected. Its accelerator throughput is not a benchmark for this application.

## Code and boundaries

- Keep API transport, validation, vault storage, retrieval and rendering in their existing modules.
  UI components consume typed application data; they do not become storage or indexing engines.
- Validate external input at the boundary. Keep JSON Schema canonical and regenerate both language
  bindings together. Never turn an API failure into a successful persistent write.
- Give resources an owner and a lifetime: SQLite connections close before Windows file replacement;
  subscriptions, timers, animation frames and GPU resources are cleaned up by their creating scope.
- Extract functions around independently testable behavior. Prefer direct code and descriptive names
  over generic frameworks. Explain units, invariants and non-obvious tradeoffs in comments.
- Format modified Python files with `uv run ruff format <paths>` and run the shared Ruff lint rules
  (`E`, `F`, `UP`, `B`, `SIM`, `I`, `ANN`, the reference's rule families). Public functions are annotated.
  Keep language-specific formatting consistent; avoid unrelated formatting churn.
- Preserve evidence identity, timestamp offsets, confidence and fidelity. Unknown locations stay
  unknown. Synthetic content and measured telemetry must be distinguishable in the UI and reports.

## Work on frequently executed paths

- Profile the relevant workload before optimizing. Record data size, revision/source hashes,
  runtime, hardware or renderer, warmup, sample count and measurement boundaries.
- Group records once instead of scanning a whole day once per visit or screenshot. Parse timestamps
  once per lookup structure. Batch database writes inside one transaction.
- Read day files through `Vault.snapshot(day)`. It parses and validates each file once per change.
  Request paths must not re-read files. A new derived cache keys on `Vault.snapshot_key(day)` plus
  the stamp of every other file it reads, and writes go through `Vault.write_json`/`write_lines` so
  the write counter moves.
- Query sorted data by bisection (`EventSeries.window`, `makeLayerLookup`, `upperBound`) rather
  than filtering a whole day per request or per frame.
- Frame callbacks allocate nothing; they write into objects they own. Build GPU buffers at their
  exact size when their inputs change, and dispose them in the same scope. Batch objects that
  share a material into one `InstancedMesh` or `lineSegments`. Do not pass freshly computed arrays
  to components that own GPU resources: drei `<Line>` rebuilds its material when `points` changes.
- Pass stable callbacks to memoized components. When only handlers need a high-frequency value
  such as the playhead time, keep it in a ref.
- Logs only grow: add records with `Vault.append_lines`, never by reading the log and writing it
  back. A rewrite per batch makes a day of capture quadratic.
- Every cache is bounded. Per-day caches use `SegmentedLRU` with a byte budget. A cache that
  grows with the number of days is a leak on a vault that is years old.
- Write streams that machines produce with `write_json(..., compact=True)`. Indentation doubles a
  1 Hz skeleton. Finished days are compressed with `daycube compact`; read through `read_text`,
  `read_json` or `read_lines`, never `Path.read_text`, so compacted files stay readable.
- API payloads carry what their consumer reads. The full record stays available from its own
  route. See [storage and caching](storage.md) for the measured budget.
- Reuse geometry, materials and mutable animation buffers where profiling identifies allocation cost.
  Keep pointer and frame updates out of React state when React does not need to render them.
- Paused and hidden views should stop drawing. Changes to the camera, time or scene must still redraw.
  Cache invalidation must cover every input that affects the cached output.
- Bound work and memory by an explicit input size or capacity. Add cancellation/backpressure before
  enabling continuous capture, reconstruction or cloud processing.
- Introduce native, vector or GPU dependencies only when a measured bottleneck justifies them.

## Evidence required for a change

For logic changes, add focused tests for realistic failures and invariants. For performance changes,
also capture before/after measurements on the same machine and compare results, not just timings.
Keep correctness checks deterministic; use timing reports for machine-dependent latency rather than
flaky wall-clock assertions in ordinary unit tests.

The standard applies to the whole repository, including the viewer, shared API client,
capture/gaze environments, extensions, scripts and optional jobs. The common deterministic
gate is `npm run check`; CI runs it on Windows and Linux. Browser suites add interaction and
rendering checks. See the [codebase review](efficiency-review.md) for coverage, measurements
and unresolved limits. Performance benchmarks remain separate from this gate.

The gate also runs graph-label buffer regressions and model-free speech tests. Speech canaries
remain opt-in in their own environment; the core environment skips optional speaker clustering
when scikit-learn is absent. Inventory the tracked review scope with
`uv run python scripts/review-inventory.py --tracked-only` to preserve unrelated local drafts.

Transport owns stream readers: process complete events incrementally, bound event buffering,
validate final payloads against canonical contracts, and cancel/release readers on early exit.
Do not buffer a whole stream before using an early event or repeat a failed request as a
different transport path without an explicit retry policy.

```powershell
npm run lint:api
npm run test:api
npm run build
npm run test:e2e:api
npm run test:e2e:viewer
npm run bench:core -- --output test-results/core-current.json
node scripts/profile-viewer.mjs current playback   # with the dev server running
```

Run checks relevant to the changed surface. The CI workflow runs contracts, API tests, frontend
build and browser checks. Browser performance tests enforce idle stopping and interactive redraws.
CPU benchmark results remain separate from correctness tests; optional AI/cloud dependencies are
not required for core tests.

Timing budgets must name the workload and target device. The viewer invariants are:

- zero draws when idle after settling;
- no shader links or buffer allocations during playback;
- at most 8 main-pass draw calls for the sample scene.

CPU baselines are in `eval/results/core-before.json` and `core-after.json`, and for the second pass
`core-snapshot-before.json` and `core-snapshot-after.json`. They are synthetic measurements, not
universal service latency guarantees. Interactive frame-time,
memory growth and large payload budgets still need traces on representative user hardware.

## Remaining priorities

1. Profile orbiting, playback and long sessions on the user's real GPU, in both development and
   production builds. Idle optimization does not establish interactive frame-rate performance.
2. Split the largest viewer components along data, scene and interaction boundaries as those areas
   change. Pins, transit arcs and the venue overlay are already separate memoized components. Preserve the current visual design and verify the affected behavior in a browser.
3. Remove remaining prototype telemetry and inspector fallbacks from live mode; report unavailable
   features and failed writes explicitly.
4. Add bounded ingestion and cancellation before expanding the capture pipeline. Add scale and
   memory benchmarks as real capture data replaces the fixture workload.

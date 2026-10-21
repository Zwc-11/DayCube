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
- Format modified Python files with `uv run ruff format <paths>` and run the shared Ruff lint rules.
  Keep language-specific formatting consistent; avoid unrelated formatting churn.
- Preserve evidence identity, timestamp offsets, confidence and fidelity. Unknown locations stay
  unknown. Synthetic content and measured telemetry must be distinguishable in the UI and reports.

## Work on frequently executed paths

- Profile the relevant workload before optimizing. Record data size, revision/source hashes,
  runtime, hardware or renderer, warmup, sample count and measurement boundaries.
- Group records once instead of scanning a whole day once per visit or screenshot. Parse timestamps
  once per lookup structure. Batch database writes inside one transaction.
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

```powershell
npm run lint:api
npm run test:api
npm run build
npm run test:e2e:api
npm run test:e2e:viewer
npm run bench:core -- --output test-results/core-current.json
```

Run checks relevant to the changed surface. The CI workflow runs contracts, API tests, frontend
build and browser checks. Browser performance tests enforce idle stopping and interactive redraws.
CPU benchmark results remain separate from correctness tests; optional AI/cloud dependencies are
not required for core tests.

Timing budgets must name the workload and target device. The current viewer invariant is zero idle
draws after settling. CPU baselines are in `eval/results/core-before.json` and `core-after.json`;
they are synthetic measurements, not universal service latency guarantees. Interactive frame-time,
memory growth and large payload budgets still need traces on representative user hardware.

## Remaining priorities

1. Profile orbiting, playback and long sessions on the user's real GPU, in both development and
   production builds. Idle optimization does not establish interactive frame-rate performance.
2. Split the largest viewer components along data, scene and interaction boundaries as those areas
   change. Preserve the current visual design and verify the affected behavior in a browser.
3. Remove remaining prototype telemetry and inspector fallbacks from live mode; report unavailable
   features and failed writes explicitly.
4. Add bounded ingestion and cancellation before expanding the capture pipeline. Add scale and
   memory benchmarks as real capture data replaces the fixture workload.

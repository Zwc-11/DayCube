# Final algorithm pass, 2026-10-24

This pass improves history pause filtering and reading-decoder geometry evaluation. It builds on
the [codebase review](review-2026-10-24.md); it is a focused follow-up, not another exhaustive audit
or evidence that every algorithm has reached its maximum speed.

## Reference and measurement

DeepInfer HEAD was checked again: `9bd9cf243fc53ef9afbf8f04d391d989117d6ab9`. Its
[CUDA graph runner](https://github.com/deepseek-ai/DeepInfer/blob/9bd9cf243fc53ef9afbf8f04d391d989117d6ab9/deepinfer/worker/cuda_graph_runner.py)
was read in full. The applicable principles are separating
preparation from repeated execution, sharing equivalent work and giving temporary state an owner.
DAYCUBE does not adopt its CUDA execution or claim comparable inference throughput.

The baseline is `33c97030ba8fac88b916746ee934add73259ebe5`. Both reports identify that base
revision and hash their actual working-tree sources, including the unchanged benchmark harness:
[before](../eval/results/capture-algorithms-before.json),
[after](../eval/results/capture-algorithms-after.json). Measurements ran sequentially on this
Windows PC, Ryzen 7 9800X3D (8 cores / 16 logical processors), Python 3.11.9. Each workload has
one warmup, eleven wall/CPU samples, a separate tracemalloc call and a separate cProfile call.

| Synthetic workload | Before median | After median | Before / after sample p95 |
| --- | ---: | ---: | ---: |
| 20,000 visits, 360 pauses, 90 days | 470.39 ms | 241.72 ms | 585.75 / 360.24 ms |
| 200 fixations, 30 lines, warm layout | 171.73 ms | 170.66 ms | 210.27 / 213.84 ms |
| 200 fixations, 60 lines in two aligned columns, warm layout | 342.55 ms | 286.01 ms | 382.32 / 335.23 ms |
| 200 fixations, 30 lines, cold layout | 171.31 ms | 170.76 ms | 206.25 / 210.47 ms |

History import took about 49% less wall time on this fixture; median process CPU fell from
359.38 to 140.63 ms. The workload includes grouping, sorting, ninety atomic file rewrites and
reading/hashing their bytes. It substitutes deterministic visits for browser SQLite reading, so
it does not measure database copying or decoding. Filesystem flush time varied between the
profiles; the entire wall-time difference cannot be attributed solely to the interval index.
All 15,000 retained records, IDs, timestamps, order and file bytes produce the same output hash.

Two-column reading took about 17% less time (roughly 1.71 to 1.43 ms per fixation, including
the benchmark's posterior collection, best-line and offset queries). Single-column results show
no meaningful speedup; their sampled p95 is slightly higher. All four workload hashes match,
including each fixation's marginal probabilities, selected line, confidence and offset, and the
final full offset posterior. These are synthetic fixations, not measured webcam FPS or improved
gaze accuracy. The processing target remains 30 FPS.

Traced peaks were 316,752 / 320,069 bytes for history, 552,688 / 553,312 for warm single-column,
1,146,592 / 1,147,264 for two-column, and 564,880 / 565,552 for cold single-column. These include
benchmark output retention and are not process RSS or isolated decoder scratch budgets.
Eleven samples do not establish production tail latency, and no GPU or end-to-end capture
latency measurement was made.

## Implementation and correctness

- `capture/history.py`: build one sorted, merged union of pause intervals per import, then
  binary-search it for each visit. Pause filtering changes from O(visits × pauses) to
  O(pauses log pauses + visits log pauses), with O(pauses) preparation storage. The profile's
  6,322,580 generator evaluations disappear. Intervals remain half-open; offsets, overlapping and
  out-of-order spans, open pauses, and empty/backwards intervals retain the previous union semantics.
  The index ends with the import and is rebuilt from the current log on the next import.
- `capture/reading.py`: share stay/return densities for identical transition geometry, and
  horizontal/vertical densities for identical emission geometry. The dictionaries exist for one
  fixation only, so new coordinates, layout, sigma, viewport or offset hypotheses cannot reuse stale
  results. Scratch storage is bounded by that fixation's candidate geometry and the 31-point grid.
  Probability arithmetic and accumulation order remain unchanged. Two-column `_inside` calls fall
  from 419,422 to 187,795. The overall model remains O(candidate lines × offset hypotheses).
- Focused tests compare interval membership with the original union predicate for sparse/dense,
  overlapping, unsorted, duplicate and invalid spans; exercise microsecond boundaries, time offsets,
  open pauses and actual history imports; and verify density sharing and recomputation after gaze
  and layout changes. Existing reading accuracy, eviction, remeasurement and off-text tests pass.

Repeat without reading personal data or opening the camera:

```powershell
uv run python eval/benchmark_capture_algorithms.py --output test-results/capture-algorithms.json --compare eval/results/capture-algorithms-before.json
```

The harness, both changed production modules and both affected test modules were reviewed for
this pass; their review notes are refreshed in the [file ledger](review-ledger.json). The broader
review's outstanding viewer/GPU, large-vault freshness, resource and manual device-acceptance
limits still apply. Variable-pitch line-band ordering and extreme multiline layouts still need
adversarial fixtures; copying browser SQLite during checkpoints still needs consistency testing.
No live capture service was restarted, and no vault contents were committed.

## Validation

`npm run check` passed: Ruff, schema consistency, 4 contract tests, 9 transport tests, 228 core
tests, 60 gaze tests, 10 extension unit tests, TypeScript and the production viewer build. A second
sparse/dense parameter case was then added; all 20 focused capture-tools/reading tests and a full
rerun of 229 core tests passed.
The API browser suite passed both tests against its isolated synthetic vault. Existing dependency
deprecation warnings and the 833 kB Three.js chunk warning remain. Receiver changes take effect
on its next normal restart; this pass did not interrupt the camera or live recording.

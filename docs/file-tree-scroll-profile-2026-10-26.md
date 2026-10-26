# FileTree synthetic scroll profile

On Windows 10.0.26200 x64, Ryzen 7 9800X3D, Node 24.11.1 and headless Chromium 156.0.8078.4,
`node scripts/benchmark-file-tree.mjs <output.json>` loaded an isolated development Vite fixture.
It used 25,000 or 100,000 generated root notes and alternated scroll positions. Each case used
10 warmup updates and 60 measured updates. React Profiler's `actualDuration` measures component
render work, not frame time, input latency, initial tree construction, memory use or GPU work.
The runs did not access the vault. The original FileTree was from revision
`be42f3525b684c54bc276a1f62459f231d07393d`. Source SHA-256 hashes and the output hashes are in
the two [before](../eval/results/file-tree-before-2026-10-26.json) and
[after](../eval/results/file-tree-after-2026-10-26.json) reports.

| Synthetic notes | Selection | Before p50 / p95 ms | After p50 / p95 ms |
| --- | --- | --- | --- |
| 25,000 | None | 0.7 / 1.1 | 0.6 / 0.9 |
| 25,000 | Last | 0.7 / 1.1 | 0.6 / 0.9 |
| 100,000 | None | 1.2 / 1.8 | 0.6 / 0.9 |
| 100,000 | Last | 1.4 / 2.0 | 0.6 / 0.9 |

The saved hashes match for the rendered row positions, titles and selection attributes over the
measured scrolls. The current-note lookup now recomputes when the rows or current path change,
instead of on every scroll. A browser regression checks selection, row changes, click and keyboard
navigation separately. Initial building and sorting remain proportional to the number of notes,
and selecting a different note still scans the rows. The fixture is a synthetic single folder and
the development profiler results do not establish production latency or application-wide parity
with any other software.

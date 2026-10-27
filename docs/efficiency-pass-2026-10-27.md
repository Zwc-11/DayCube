# Camera interpolation and repository validation

Continues the [prior pass](efficiency-followup-2026-10-26.md) from DAYCUBE
`2ffd223a35e7745723d416b894f1c96012cdc687`. The DeepInfer repository and its pinned
`553741557a57df44a46fb3ab72b6aafeaf945337` CUDA graph runner were revisited: preparing owned
output storage before replay is the relevant pattern. This is an implementation reference,
not a reproduction of its accelerator throughput or a new exhaustive dependency audit.

## Measured change

Both mesh and splat following allocated a result object, an interpolation closure and three arrays
on each pose lookup. Each live viewer now owns one pose object with two three-element arrays;
the shared interpolation routine writes into them. The existing snapshot API still returns fresh
storage when no output is supplied. Startup snapshots and live buffers cannot overwrite each other.
Search order, duplicate-time policy, half-second settling offset, clamping, floating-point
arithmetic and direction normalization are preserved. Source camera arrays are read only.

Synthetic Node 24.11.1 CPU benchmark on Windows build 26200, Ryzen 7 9800X3D: 12,000 poses,
200,000 evaluations per sample, two warmups and nine measured samples. Profiling is disabled during
timing. Both versions produce the same sampled pose hash and per-run numeric checksum.

| Measurement | Before | After |
| --- | ---: | ---: |
| Median per 200,000 evaluations | 23.311 ms | 6.272 ms |
| Maximum of nine samples | 23.909 ms | 6.505 ms |
| Distinct result objects retained by 1,000-call probe | 1,000 | 1 |
| Distinct output arrays retained by that probe | 2,000 | 2 |

This is 3.7 times faster for interpolation alone. The absolute saving per lookup is small;
this does not predict website FPS, real GPU latency, browser allocation totals or RSS. The identity
probe counts retained outputs, not total intermediate allocations. CPU time has coarse Windows
resolution. Nine observations do not establish a production tail distribution.

Reproduce with `node eval/benchmark_camera_pose.mjs <source> <output.json>`. For before, export
`apps/viewer/src/components/modals/SplatView.tsx` from the baseline commit to an isolated file;
after uses `apps/viewer/src/lib/cameraPose.ts`. The script transpiles the function/module, never
reads the vault, and records runtime, hardware, revision and source/benchmark SHA-256 hashes.
Reports: [before](../eval/results/camera-pose-before-2026-10-27.json) and
[after](../eval/results/camera-pose-after-2026-10-27.json).

## Correctness and broader scope

Focused cases compare the previous algorithm for single/short paths, duplicate timestamps,
zero directions, endpoint and nonfinite queries, input immutability, reusable buffer identity and
independent snapshots. These run in the common viewer unit gate.

The default repository check initially failed to collect the new memory/single-photo tests because
they import Pillow directly while it was only declared in an optional runtime extra. Pillow is now
also a development dependency; the lockfile keeps the existing resolved version. No model weights
or paid workloads are needed for this correction.

The new memory view also failed TypeScript checking because a dynamically imported `THREE` value
was used as a type namespace in a matrix argument assertion. A type-only `import('three')`
reference fixes the check without changing emitted runtime behavior.

The preceding file review remains historical. The newer memories/reconstruction feature adds
substantial code after that review; this pass does not label it all manually audited. The ledger
retains stale or missing review notes explicitly. Cold startup/vendor chunks, reconstruction/model
resource use, capture multi-log atomicity, long-session RSS and native camera acceptance remain open.
No live capture or recordings were accessed for the benchmarks.

The refreshed inventory has 558 tracked entries excluding the ledger. Current scoped manual notes
cover 229 of 246 source files, 86 of 92 tests and 41 of 49 configuration files. Thus 31 entries
in these categories need a new or refreshed semantic review, predominantly newer reconstruction
and memories work. Generated code, locks, assets, fixtures, reports and documents retain separate
treatment. Running the common gate is not a substitute for those remaining reviews.

## Validation

After the dependency/type corrections, `npm run check` passed: 381 backend tests (13 skips),
147 gaze tests, contracts, transport, extension and viewer unit tests, model-free speech tests,
TypeScript and the production build. The backend suite took 218.58 seconds on this machine;
the reconstruction cases are substantial native integration work. All 43 synthetic-vault viewer
browser tests passed, including playback, idle/modal stopping and memories. Large vendor-chunk
warnings and existing dependency deprecation warnings remain. The pre-existing `package-lock.json`
metadata edit was preserved and excluded from this change.

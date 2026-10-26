# Efficiency and upload integrity follow-up

This follows the [previous review](codebase-continuation-2026-10-26.md) at revision
`be42f3525b684c54bc276a1f62459f231d07393d`. The [DeepInfer review](deepinfer-review.md)
records the reference revision and files examined. These changes apply its ownership, bounded-work
and measurement principles to different workloads; they do not establish cross-application parity.

## Capture-only history rebuild

Rebuilding each capture-only date scanned every capture directory to decide whether that one date
existed. `LifeStore.update` now checks the requested date directly, preserving the existing valid-date
and plain/compacted-log rules. Full discovery and the existing two-second freshness window are
unchanged. Focused tests cover invalid and missing dates, compacted names and direct indexing without
a history scan.

On Windows build 26200, Ryzen 7 9800X3D and Python 3.11.9, `eval/benchmark_capture_history.py`
creates 100 synthetic capture-only days with one search each, rebuilds a new SQLite index after one
warmup, and times three samples. It compares every indexed unit column, the numeric timestamp and
text hash. The outputs have identical SHA-256 values in the [before](../eval/results/capture-history-before-2026-10-26.json)
and [after](../eval/results/capture-history-after-2026-10-26.json) reports. Each run uses a new
database and synthetic vault; the OS file cache is warm.

| Measurement | Before | After |
| --- | ---: | ---: |
| Median elapsed | 2,135 ms | 1,274 ms |
| Maximum of three samples | 2,726 ms | 1,320 ms |
| Median process CPU | 1,625 ms | 750 ms |
| Traced peak Python allocations | 438 KB | 436 KB |

The baseline profile called the whole-history enumeration 101 times; after, it runs once for
discovery. Three samples are insufficient to characterize production tail latency, and traced
allocations are not RSS. Full freshness checks still inspect each day's source files; SQLite writes,
concurrent indexing, larger log contents and cold storage remain separate costs.

Run `uv run python eval/benchmark_capture_history.py --days 100 --repeats 3 --output <report.json>`.
The reports contain the exact source hashes. To reproduce the baseline, use the benchmark script
from this change with the original ingest sources from the revision above in an isolated checkout.

## Phone upload integrity and retry

An existing staged file was accepted solely because its size matched; different evidence of the
same size could be silently skipped. A matching file is now streamed through SHA-256 with a bounded
buffer, and its identity is checked again before acknowledgement. Hashing on upload start is outside
the shared lock. Durable per-upload receipts allow completion retries after a lost response or item
publication; tests also exercise cross-device access, failed receipt writes, stale receipts and file
replacement during verification.

This is a correctness fix with a measured cost. On the same Windows/Python machine, seven warm
samples after two warmups using synthetic staged files produced these medians:

| File size | Previous size-only check | Verified check | Verified traced peak |
| --- | ---: | ---: | ---: |
| 1 MiB | 0.079 ms | 1.741 ms | 1.06 MB |
| 64 MiB | 0.062 ms | 39.466 ms | 2.11 MB |

The earlier result did not establish content identity. Cold-storage latency, concurrent devices and
the maximum 8 GiB file were not measured. Receipts intentionally persist and grow with completed
uploads; an eventual retention policy must preserve retry guarantees. Completion hashing and rare
receipt recovery still hold the shared lock. File stamps detect ordinary concurrent changes, not
an adversary able to restore filesystem metadata. The synthetic [before](../eval/results/phone-integrity-before-2026-10-26.json)
and [after](../eval/results/phone-integrity-after-2026-10-26.json) results include source hashes;
the benchmark script SHA-256 is `1873cc73f701e0b7aa7566d4550165731ef81346771e279e2a5527ec69dfd533`.
Run `uv run python eval/benchmark_phone_integrity.py`; `DAYCUBE_PHONE_BENCH_SOURCE` selects an
isolated copy of the old module for a baseline. No actual phone content is used.

## Viewer scrolling and remaining scope

The [FileTree profile](file-tree-scroll-profile-2026-10-26.md) records the synthetic scroll
measurements, output comparison and interaction regression. In a 100,000-note tree with the last
note selected, median React development render duration fell from 1.4 ms to 0.6 ms; this is not
end-to-end frame time. Initial tree construction, real GPU behavior and long-session memory remain
unmeasured. Other gaps in the previous review remain open; this follow-up is not an exhaustive
new audit of every dependency or historical pull request.

## Validation

`npm run check` passed with FFmpeg on PATH: 365 core tests passed with seven platform skips,
147 gaze tests passed, extension and viewer unit tests passed, model-free speech tests passed
with four optional skips, and the production build completed. The synthetic-vault browser suites
passed 41 viewer and two API cases. The receiver source archive and wheel each contained 18 Python
modules and no runtime data. A first viewer-browser invocation without FFmpeg could not start its
fixture server; it passed after FFmpeg was added to that process's PATH. The build still warns
about large vendor chunks, which remain a startup measurement gap.
